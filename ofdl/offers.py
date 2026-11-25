"""Free-page and free-trial discovery plus guarded zero-price claiming.

Detection follows the same price semantics the community OF-Scraper tool uses:

* a page is free when its regular price is ``0``;
* a free trial is a ``0`` current price or a claimable ``0`` promotion on a
  page whose regular price is paid.

Only data the account can already read is used (active/expired subscriptions,
the account's own chats, publicly readable profiles). Claiming is limited to
zero-price rows: free pages are re-read immediately before the POST, shared
trial links are claimed by their code, and POSTs are never retried. A price
change between the final check and the server's decision cannot be fully
excluded, so callers must obtain explicit confirmation first.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from .api import ApiClient, ApiError
from .common import AppError, Cancelled, Control, EventSink, no_events
from .settings import atomic_json, config_dir

TRIAL_LINK_RE = re.compile(
    r'https://onlyfans\.com/([A-Za-z0-9_.-]{1,80})/trial/([A-Za-z0-9_-]{1,128})',
    re.IGNORECASE)
MESSAGE_PAGE_SIZE = 50
MESSAGE_PAGES = 3
MAX_CHATS = 120
MAX_CHAT_PAGES = 10
MAX_INBOX_PROFILES = 80
MAX_LISTS = 20
LIST_PAGE_SIZE = 50
MAX_LIST_USERS = 400
MAX_PROMOTION_BANNERS = 25
HISTORY_LIMIT = 2000
_FAILED_SOURCE = object()

FREE = 'free'
TRIAL = 'trial'
SUBSCRIBED = 'subscribed'
AVAILABLE = 'available'
USED = 'used'


@dataclass
class Offer:
    """One scanned free page or trial candidate for website review."""

    key: str
    username: str
    kind: str
    source: str
    detail: str
    status: str = AVAILABLE
    user_id: str = ''
    code: str = ''
    price: float = 0.0
    regular_price: float = 0.0

    @property
    def label(self) -> str:
        return 'Free page' if self.kind == FREE else 'Free trial'

    @property
    def actionable(self) -> bool:
        """Only zero-price rows with a known claim path can be sent.

        Free pages use the subscribe endpoint; trials need their shared code.
        A trial that only appears as a reduced profile price has no verified
        claim route and stays website-only.
        """
        if not self.available or self.price != 0:
            return False
        return bool(self.user_id) if self.kind == FREE else bool(self.code)

    @property
    def available(self) -> bool:
        """A scanned zero-price candidate that has not been used yet."""
        return self.status == AVAILABLE

    @property
    def reason(self) -> str:
        if self.status == SUBSCRIBED:
            return 'Already subscribed'
        if self.status == USED:
            return 'Already claimed'
        if self.kind == TRIAL and not self.code:
            return 'Trial offer needs website review'
        return 'Ready to subscribe' if self.kind == FREE else 'Ready to claim'

    @property
    def url(self) -> str:
        if self.kind == TRIAL and self.code:
            return f'https://onlyfans.com/{self.username}/trial/{self.code}'
        return f'https://onlyfans.com/{self.username}'


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number < 0 or number == float('inf'):
        return None
    return number


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ''


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _username(value: Any) -> str:
    name = _text(value).strip().lstrip('@')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', name) or name in {'.', '..'}:
        return ''
    return name


def trial_links(text: str) -> list[tuple[str, str]]:
    """Return unique ``(username, code)`` pairs found in free-text/HTML."""
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in TRIAL_LINK_RE.finditer(_text(text)):
        name = _username(match.group(1))
        code = match.group(2)
        if not name or (name, code) in seen:
            continue
        seen.add((name, code))
        found.append((name, code))
    return found


def classify(row: Mapping[str, Any], *, source: str, status: str = AVAILABLE) -> Offer | None:
    """Return a free/trial offer for a subscription or profile row, else ``None``.

    ``row`` is the raw API object from ``/subscriptions/subscribes`` or a
    profile. Paid offers are deliberately not returned.
    """
    if not isinstance(row, dict):
        return None
    name = _username(row.get('username'))
    user_id = row.get('id')
    if not name or user_id is None:
        return None
    user_id = str(user_id)
    if not user_id.isdigit():
        return None
    subscribed_data = _mapping(row.get('subscribedByData'))
    promotions = row.get('promotions')
    promotions = promotions if isinstance(promotions, list) else []
    claimable = [item for item in promotions
                 if isinstance(item, dict) and item.get('canClaim') is True]
    claimable_prices = [value for value in (_number(item.get('price')) for item in claimable)
                        if value is not None]
    current = _number(row.get('currentSubscribePrice'))
    if current is None:
        current = _number(subscribed_data.get('currentPrice'))
    regular = _number(row.get('subscribePrice'))
    if regular is None:
        regular = _number(subscribed_data.get('regularPrice'))

    if regular is None:
        return None
    if regular == 0:
        return Offer(key=f'free:{user_id}', username=name, kind=FREE, source=source,
                     detail='Free subscription page', status=status,
                     user_id=user_id, price=0.0, regular_price=0.0)
    trial_price: float | None = None
    if current == 0:
        trial_price = 0.0
    elif any(price == 0 for price in claimable_prices):
        trial_price = 0.0
    if trial_price is None:
        return None
    days = ''
    for item in claimable:
        price = _number(item.get('price'))
        if price == 0:
            duration = _number(item.get('subscribeDays'))
            if duration:
                days = f' · {int(duration)} days'
            break
    return Offer(key=f'trial:{user_id}', username=name, kind=TRIAL, source=source,
                 detail=f'Free trial offer{days}', status=status, user_id=user_id,
                 price=0.0, regular_price=regular)


def _source_failure(source: str, exc: AppError, emit: EventSink) -> None:
    # Cancellation, expired authentication and rate limits stop the scan; a
    # missing optional endpoint does not discard results from other sources.
    if isinstance(exc, Cancelled) or (isinstance(exc, ApiError) and exc.status in {401, 429}):
        raise exc
    detail = f'HTTP {exc.status}' if isinstance(exc, ApiError) else str(exc)
    message = f'{source}: incomplete source ({detail}); keeping results already found.'
    emit('log', message)
    emit('offer_warning', {'source': source, 'message': message})


def _source_request(client: ApiClient, endpoint: str, params=None, *, source: str,
                    emit: EventSink):
    try:
        return client.request(endpoint, params)
    except AppError as exc:
        _source_failure(source, exc, emit)
        return _FAILED_SOURCE


def _subscription_rows(client: ApiClient, control: Control, state: str,
                       emit: EventSink) -> Iterable[Mapping[str, Any]]:
    emit('log', f'Reading {state} subscriptions to look for free pages and trials.')
    try:
        for row in client.paginate('/subscriptions/subscribes', 'subscriptions',
                                   subscription_type=state):
            control.checkpoint()
            if isinstance(row, dict):
                yield row
    except AppError as exc:
        _source_failure(f'{state} subscriptions', exc, emit)


def _chat_offers(client: ApiClient, control: Control, username: str,
                 emit: EventSink, pages: int = MESSAGE_PAGES) -> Iterable[Offer]:
    """Look at the newest messages of one chat for shared trial links."""
    cursor: str | None = None
    for _ in range(pages):
        control.checkpoint()
        params: dict[str, Any] = {'limit': MESSAGE_PAGE_SIZE, 'order': 'desc'}
        if cursor:
            params['id'] = cursor
        try:
            data = client.request(f'/chats/{username}/messages', params)
        except AppError as exc:
            _source_failure(f'Chat with @{username}', exc, emit)
            return
        if isinstance(data, list):
            rows, has_more = data, False
        elif isinstance(data, dict) and isinstance(data.get('list'), list):
            rows, has_more = data['list'], data.get('hasMore') is True
        else:
            _source_failure(f'Chat with @{username}', AppError('Unexpected response format'), emit)
            return
        if not rows:
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            for name, code in trial_links(_text(row.get('text')) + ' ' + _text(row.get('rawText'))):
                yield Offer(key=f'trialcode:{OfferHistory.code_token(code)}', username=name,
                            kind=TRIAL, source=f'chat @{username}', code=code,
                            detail='Free trial link shared in a message')
        if not has_more:
            return
        cursor = str(rows[-1].get('id', ''))
        if not cursor:
            return


def _inbox_names(client: ApiClient, control: Control, emit: EventSink,
                 max_pages: int = MAX_CHAT_PAGES) -> list[str]:
    """Recent chat partners from the inbox, newest first.

    Only tiny identifiers are read; no message text is touched here.
    """
    names: list[str] = []
    seen: set[str] = set()
    offset = 0
    for _ in range(max_pages):
        control.checkpoint()
        data = _source_request(client, '/chats', {'limit': MESSAGE_PAGE_SIZE, 'offset': offset,
                                                'order': 'recent'}, source='Inbox', emit=emit)
        if data is _FAILED_SOURCE:
            break
        rows = data.get('list') if isinstance(data, dict) else data
        if not isinstance(rows, list):
            _source_failure('Inbox', AppError('Unexpected response format'), emit)
            break
        if not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in ('withUser', 'user', 'correspondent'):
                user = row.get(key)
                if isinstance(user, dict):
                    name = _username(user.get('username'))
                    if name and name not in seen:
                        seen.add(name)
                        names.append(name)
                    break
        emit('offer_progress', {'phase': 'inbox', 'done': len(names)})
        has_more = data.get('hasMore') if isinstance(data, dict) else None
        if has_more is False:
            break
        offset += len(rows)
        if has_more is None and len(rows) < MESSAGE_PAGE_SIZE:
            break
    return names


def _list_rows(client: ApiClient, control: Control, emit: EventSink,
               max_lists: int = MAX_LISTS,
               max_users: int = MAX_LIST_USERS) -> Iterable[dict[str, Any]]:
    """Users from the account's own custom lists (favorites and similar)."""
    list_ids: list[str] = []
    offset = 0
    for _ in range(4):
        control.checkpoint()
        data = _source_request(client, '/lists', {'limit': LIST_PAGE_SIZE, 'offset': offset},
                               source='Custom lists', emit=emit)
        if data is _FAILED_SOURCE:
            break
        rows = data.get('list') if isinstance(data, dict) else data
        if not isinstance(rows, list):
            _source_failure('Custom lists', AppError('Unexpected response format'), emit)
            break
        if not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            list_id = str(row.get('id', ''))
            if list_id.isdigit() and list_id not in list_ids:
                list_ids.append(list_id)
        has_more = data.get('hasMore') if isinstance(data, dict) else None
        if has_more is False or len(list_ids) >= max_lists:
            break
        offset += len(rows)
        if has_more is None and len(rows) < LIST_PAGE_SIZE:
            break
    list_ids = list_ids[:max_lists]
    emit('offer_progress', {'phase': 'lists', 'done': 0, 'total': len(list_ids)})
    seen: set[str] = set()
    for number, list_id in enumerate(list_ids, start=1):
        control.checkpoint()
        offset = 0
        for _ in range(8):
            control.checkpoint()
            data = _source_request(client, f'/lists/{list_id}/users',
                                   {'limit': LIST_PAGE_SIZE, 'offset': offset},
                                   source=f'Custom list {list_id}', emit=emit)
            if data is _FAILED_SOURCE:
                break
            rows = data.get('list') if isinstance(data, dict) else data
            if not isinstance(rows, list):
                _source_failure(f'Custom list {list_id}', AppError('Unexpected response format'), emit)
                break
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = _username(row.get('username'))
                if name and name not in seen:
                    seen.add(name)
                    yield row
                    if len(seen) >= max_users:
                        emit('offer_progress', {'phase': 'lists', 'done': number,
                                                'total': len(list_ids)})
                        return
            has_more = data.get('hasMore') if isinstance(data, dict) else None
            if has_more is False:
                break
            offset += len(rows)
            if has_more is None and len(rows) < LIST_PAGE_SIZE:
                break
        emit('offer_progress', {'phase': 'lists', 'done': number, 'total': len(list_ids)})


def _promotion_offers(client: ApiClient, control: Control, emit: EventSink) -> Iterable[Offer]:
    """Banners the website shows to this account under "promotions".

    Only the URL of each banner is followed: a shared trial link is used
    directly, a creator profile is classified like any other profile. Banner
    images and other creative content are not downloaded.
    """
    data = _source_request(client, '/users/promotions', source='Website promotions', emit=emit)
    if data is _FAILED_SOURCE:
        return
    rows = data.get('list') if isinstance(data, dict) and isinstance(data.get('list'), list) else data
    if not isinstance(rows, list):
        _source_failure('Website promotions', AppError('Unexpected response format'), emit)
        return
    emit('log', f'Website promotions: {len(rows)} banner(s) available.')
    profiles: list[str] = []
    ignored = 0
    for row in rows[:MAX_PROMOTION_BANNERS]:
        control.checkpoint()
        if not isinstance(row, dict):
            continue
        url = _text(row.get('url'))
        links = trial_links(url)
        if links:
            for name, code in links:
                yield Offer(key=f'trialcode:{OfferHistory.code_token(code)}',
                            username=name, kind=TRIAL, source='website promotion', code=code,
                            detail='Free trial link from a website promotion')
            continue
        try:
            parsed = urlsplit(url)
        except ValueError:
            ignored += 1
            continue
        if parsed.scheme != 'https' or (parsed.hostname or '').lower() not in {'onlyfans.com', 'www.onlyfans.com'}:
            ignored += 1
            continue
        parts = [part for part in parsed.path.split('/') if part]
        if len(parts) == 1:
            name = _username(parts[0])
            if name:
                profiles.append(name)
        else:
            ignored += 1
    profiles = list(dict.fromkeys(profiles))[:MAX_PROMOTION_BANNERS]
    for index, name in enumerate(profiles, start=1):
        control.checkpoint()
        try:
            profile = client.user(name)
        except AppError as exc:
            _source_failure(f'Promotion profile @{name}', exc, emit)
            ignored += 1
            continue
        offer = classify(profile, source='website promotion',
                         status=SUBSCRIBED if profile.get('subscribedBy') is True else AVAILABLE)
        if offer is not None:
            yield offer
        else:
            ignored += 1
        emit('offer_progress', {'phase': 'promotions', 'done': index, 'total': len(profiles)})
    if ignored:
        emit('log', f'Website promotions: {ignored} banner(s) ignored because they are paid, '
                    f'unreadable or not zero-price. Paid offers are never listed or claimed.')


def scan_offers(client: ApiClient, control: Control, *, include_active: bool = True,
                include_expired: bool = True, include_messages: bool = True,
                include_inbox: bool = True, include_lists: bool = True,
                include_promotions: bool = True, deep: bool = False,
                extra_profiles: Iterable[str] = (), history: OfferHistory | None = None,
                emit: EventSink = no_events) -> list[Offer]:
    """Build the current free/trial list for the signed-in account.

    Active subscriptions are included so already-subscribed pages appear as
    read-only rows; they can never be subscribed again from this tab.

    ``deep`` raises the bounded chat/list limits. It is still an
    account-connected scan, never a platform-wide directory.
    """
    history = history or OfferHistory.load()
    pages = 10 if deep else MESSAGE_PAGES
    max_chats = 400 if deep else MAX_CHATS
    max_inbox_profiles = 250 if deep else MAX_INBOX_PROFILES
    max_lists = 60 if deep else MAX_LISTS
    max_list_users = 2000 if deep else MAX_LIST_USERS
    max_inbox_pages = 20 if deep else MAX_CHAT_PAGES
    if deep:        emit('log', 'Deep scan enabled: more chat pages, chat partners, custom lists and list users.')
    offers: dict[str, Offer] = {}
    chat_names: list[str] = []
    subscribed_names: set[str] = set()

    def publish(offer: Offer, removed: str | None = None) -> None:
        # Queue a snapshot: later de-duplication must not mutate a UI event
        # that is still waiting to be consumed on the Tk thread.
        emit('offer_row', {'offer': replace(offer), 'remove': removed})

    def add(offer: Offer | None) -> None:
        if offer is None:
            return
        if offer.username.casefold() in subscribed_names:
            offer.status = SUBSCRIBED
        if offer.kind == TRIAL and offer.code and history.is_claimed(offer.code):
            if offer.status != SUBSCRIBED:
                offer.status = USED
        previous = offers.get(offer.key)
        if previous is not None:
            # Active subscription state always wins over a chat/profile sighting.
            if previous.status == SUBSCRIBED:
                return
            if offer.status == SUBSCRIBED:
                offers[offer.key] = offer
                publish(offer)
            return
        # One row per creator: a free page and a trial link for the same account
        # would be the same subscription attempt twice.
        same_creator = next((item for item in offers.values()
                             if item.username.lower() == offer.username.lower()), None)
        if same_creator is not None:
            if same_creator.status == SUBSCRIBED:
                return
            if offer.status == SUBSCRIBED:
                same_creator.status = SUBSCRIBED
                publish(same_creator)
                return
            if (offer.available and not same_creator.available or
                    offer.available and same_creator.available and offer.code and not same_creator.code):
                # Prefer an unused shared link over a code-less/used trial.
                offers.pop(same_creator.key, None)
                offers[offer.key] = offer
                publish(offer, same_creator.key)
            return
        offers[offer.key] = offer
        publish(offer)

    client.user('me')
    states: list[str] = []
    if include_active:
        states.append('active')
    if include_expired:
        states.append('expired')
    for number,state in enumerate(states, start=1):
        emit('offer_progress', {'phase': 'subscriptions', 'done': number - 1, 'total': len(states)})
        for row in _subscription_rows(client, control, state, emit):
            name = _username(row.get('username'))
            active = state == 'active' or row.get('subscribedBy') is True
            if name and active:
                subscribed_names.add(name.casefold())
                chat_names.append(name)
            elif name and include_messages:
                # Expired creators can share a fresh trial link in a new chat
                # message even though the paid subscription has ended.
                chat_names.append(name)
            add(classify(row, source=f'{state} subscription',
                         status=SUBSCRIBED if active else AVAILABLE))
        emit('offer_progress', {'phase': 'subscriptions', 'done': number, 'total': len(states)})

    if include_inbox:
        emit('offer_progress', {'phase': 'inbox', 'done': 0})
        inbox = _inbox_names(client, control, emit, max_pages=max_inbox_pages)
        emit('log', f'Inbox: {len(inbox)} recent chat partner(s) found.')
        known = {offer.username.casefold() for offer in offers.values()}
        chat_names.extend(name for name in inbox if name.casefold() not in subscribed_names)
        candidates = [name for name in inbox if name.casefold() not in known]
        if len(candidates) > max_inbox_profiles:
            emit('log', f'Checking the first {max_inbox_profiles} of {len(candidates)} inbox '
                        f'profiles; the rest are covered by the message scan only.')
            candidates = candidates[:max_inbox_profiles]
        for index, name in enumerate(candidates, start=1):
            control.checkpoint()
            try:
                profile = client.user(name)
            except AppError as exc:
                _source_failure(f'Inbox profile @{name}', exc, emit)
                continue
            add(classify(profile, source='inbox chat partner',
                         status=SUBSCRIBED if profile.get('subscribedBy') is True else AVAILABLE))
            emit('offer_progress', {'phase': 'profiles', 'done': index, 'total': len(candidates)})

    if include_lists:
        emit('offer_progress', {'phase': 'lists', 'done': 0})
        for row in _list_rows(client, control, emit, max_lists=max_lists,
                              max_users=max_list_users):
            add(classify(row, source='custom list',
                         status=SUBSCRIBED if row.get('subscribedBy') is True else AVAILABLE))

    if include_promotions:
        emit('offer_progress', {'phase': 'promotions', 'done': 0})
        for offer in _promotion_offers(client, control, emit):
            add(offer)

    if include_messages and chat_names:
        names = list(dict.fromkeys(chat_names))
        if len(names) > max_chats:
            emit('log', f'Checking the newest messages of the first {max_chats} of {len(names)} chats; '
                        f'the rest are skipped to keep the scan bounded.')
            names = names[:max_chats]
        else:
            emit('log', f'Checking the newest messages of {len(names)} chat(s) for trial links.')
        for index, name in enumerate(names, start=1):
            for offer in _chat_offers(client, control, name, emit, pages=pages):
                if offer.username.casefold() in subscribed_names:
                    offer.status = SUBSCRIBED
                add(offer)
            emit('offer_progress', {'phase': 'messages', 'done': index, 'total': len(names)})

    extra_profiles = list(extra_profiles)
    for index, name in enumerate(extra_profiles, start=1):
        control.checkpoint()
        clean = _username(name)
        if not clean:
            emit('log', 'Ignored an invalid creator name in the check list.')
            continue
        try:
            profile = client.user(clean)
        except AppError as exc:
            _source_failure(f'Profile @{clean}', exc, emit)
            continue
        add(classify(profile, source='checked profile',
                     status=SUBSCRIBED if profile.get('subscribedBy') is True else AVAILABLE))
        emit('offer_progress', {'phase': 'extra', 'done': index, 'total': len(extra_profiles)})

    result = list(offers.values())
    result.sort(key=lambda offer: (offer.status != AVAILABLE, offer.kind, offer.username.lower()))
    emit('log', f'Free & trials scan finished: {len(result)} matching offer(s).')
    return result


def claim_offer(client: ApiClient, offer: Offer, control: Control,
                history: OfferHistory | None = None, emit: EventSink = no_events) -> str:
    """Subscribe a free page or claim a shared trial code.

    Safety rules, enforced for every caller:

    * only a row whose scanned price is exactly 0 is sent;
    * free pages are re-read immediately before the POST and are refused if
      they are no longer free or already subscribed;
    * trials need a shared code; a code-less reduced-price trial has no
      verified claim route and is refused;
    * POSTs are never retried (the API client already enforces this).

    A price change between the final read and the server's decision cannot be
    excluded. The UI therefore asks for explicit confirmation and warns that a
    trial auto-renews at the creator's regular price unless cancelled.
    """
    control.checkpoint()
    history = history or OfferHistory.load()
    if not offer.actionable:
        raise AppError(f'@{offer.username} is not a zero-price claimable offer. Nothing was sent.')
    name = _username(offer.username)
    if not name:
        raise AppError('The offer has no valid creator name. Nothing was sent.')
    # Verify the creator again: it also supplies the numeric ID and catches a
    # subscription that happened after the scan.
    try:
        profile = client.user(name)
    except ApiError as exc:
        if exc.status in {401, 429}:
            raise
        raise AppError(f'Could not verify @{name} before sending. Nothing was sent.') from None
    if profile.get('subscribedBy') is True:
        offer.status = SUBSCRIBED
        raise AppError(f'@{name} is already subscribed. Nothing was sent.')
    user_id = str(profile.get('id', ''))
    if not user_id.isdigit():
        raise AppError(f'@{name} did not return a valid account ID. Nothing was sent.')
    control.checkpoint()
    if offer.kind == TRIAL:
        if not offer.code:
            raise AppError('This trial has no claim code; open it on the website. Nothing was sent.')
        if history.is_claimed(offer.code):
            offer.status = USED
            raise AppError(f'That trial code for @{name} was already claimed. Nothing was sent.')
        client.post('/trials/claim', {'code': offer.code})
        history.mark_claimed(offer.code, name)
        history.mark_subscribed(user_id, name, TRIAL)
        offer.status = SUBSCRIBED
        emit('log', f'Free trial claimed for @{name}.')
        return f'Free trial claimed for @{name}'
    fresh = classify(profile, source='claim check')
    if fresh is None or fresh.kind != FREE or fresh.price != 0 or fresh.regular_price != 0:
        raise AppError(f'@{name} is no longer a zero-price page. Nothing was sent.')
    client.post(f'/users/{fresh.user_id}/subscribe')
    history.mark_subscribed(fresh.user_id, name, FREE)
    offer.status = SUBSCRIBED
    emit('log', f'Subscribed to free page @{name}.')
    return f'Subscribed to @{name}'


class OfferHistory:
    """Small local memory of free subscriptions and claimed trial codes.

    Trial codes are stored as a SHA-256 prefix only: enough to prevent a second
    claim of the same link without keeping a reusable code in plaintext.
    """

    def __init__(self, path: Path | None = None, *, claimed: list[dict[str, Any]] | None = None,
                 subscribed: list[dict[str, Any]] | None = None):
        self.path = Path(path) if path is not None else config_dir() / 'free_trials.json'
        self.claimed: list[dict[str, Any]] = list(claimed or [])
        self.subscribed: list[dict[str, Any]] = list(subscribed or [])

    @staticmethod
    def code_token(code: str) -> str:
        return hashlib.sha256(_text(code).encode('utf-8')).hexdigest()[:32]

    @classmethod
    def load(cls, path: Path | None = None) -> OfferHistory:
        history = cls(path)
        try:
            if not history.path.exists() or history.path.stat().st_size > 512 * 1024:
                return history
            values = json.loads(history.path.read_text(encoding='utf-8'))
        except (OSError, ValueError, RecursionError):
            return history
        if not isinstance(values, dict):
            return history
        claimed = values.get('claimed')
        subscribed = values.get('subscribed')
        history.claimed = [item for item in claimed if isinstance(item, dict)] \
            if isinstance(claimed, list) else []
        history.subscribed = [item for item in subscribed if isinstance(item, dict)] \
            if isinstance(subscribed, list) else []
        return history

    def save(self) -> bool:
        values = {'version': 1, 'claimed': self.claimed[-HISTORY_LIMIT:],
                  'subscribed': self.subscribed[-HISTORY_LIMIT:]}
        try:
            atomic_json(self.path, values)
            return True
        except OSError:
            return False

    def is_claimed(self, code: str) -> bool:
        token = self.code_token(code)
        return any(item.get('token') == token for item in self.claimed)

    def is_subscribed(self, user_id: str) -> bool:
        return any(str(item.get('user_id')) == str(user_id) for item in self.subscribed)

    def mark_claimed(self, code: str, username: str) -> None:
        token = self.code_token(code)
        if any(item.get('token') == token for item in self.claimed):
            return
        self.claimed.append({'token': token, 'username': _username(username),
                             'at': datetime.now(timezone.utc).isoformat(timespec='seconds')})
        self.save()

    def mark_subscribed(self, user_id: str, username: str, kind: str = FREE) -> None:
        if self.is_subscribed(user_id):
            return
        self.subscribed.append({'user_id': str(user_id), 'username': _username(username),
                                'kind': kind,
                                'at': datetime.now(timezone.utc).isoformat(timespec='seconds')})
        self.save()
