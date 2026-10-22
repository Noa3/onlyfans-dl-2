"""Free-page and free-trial offers: detection plus confirmed claim actions.

Detection follows the same price semantics the community OF-Scraper tool uses:

* a page is free when its regular price is ``0``;
* a free trial is a ``0`` current price or a claimable ``0`` promotion on a
  page whose regular price is paid.

Only data the account can already read is used (active/expired subscriptions,
the account's own chats, publicly readable profiles). Nothing is subscribed or
claimed without an explicit click and confirmation in the Free & trials tab.
No paid offer is ever acted on: offers whose price is above zero are ignored.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from .api import ApiClient, ApiError
from .common import AppError, Control, EventSink, no_events
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

FREE = 'free'
TRIAL = 'trial'
SUBSCRIBED = 'subscribed'
AVAILABLE = 'available'
USED = 'used'


@dataclass
class Offer:
    """One free page or free trial the account may act on."""

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
        """False when a row is informational only (already subscribed or no code)."""
        if self.status != AVAILABLE:
            return False
        if self.kind == FREE:
            return self.user_id.isdigit()
        return bool(self.code)

    @property
    def reason(self) -> str:
        if self.status == SUBSCRIBED:
            return 'Already subscribed'
        if self.status == USED:
            return 'Already claimed'
        if self.kind == TRIAL and not self.code:
            return 'Claim this trial on the website'
        return ''

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
    regular = _number(subscribed_data.get('regularPrice'))
    if regular is None:
        regular = _number(row.get('subscribePrice'))

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


def _subscription_rows(client: ApiClient, control: Control, state: str,
                       emit: EventSink) -> Iterable[Mapping[str, Any]]:
    emit('log', f'Reading {state} subscriptions to look for free pages and trials.')
    for row in client.paginate('/subscriptions/subscribes', 'subscriptions',
                               subscription_type=state):
        control.checkpoint()
        if isinstance(row, dict):
            yield row


def _chat_offers(client: ApiClient, control: Control, username: str,
                 emit: EventSink) -> Iterable[Offer]:
    """Look at the newest messages of one chat for shared trial links."""
    cursor: str | None = None
    for _ in range(MESSAGE_PAGES):
        control.checkpoint()
        params: dict[str, Any] = {'limit': MESSAGE_PAGE_SIZE, 'order': 'desc'}
        if cursor:
            params['id'] = cursor
        try:
            data = client.request(f'/chats/{username}/messages', params)
        except ApiError as exc:
            if exc.status in {401, 403, 404, 429}:
                emit('log', f'Chat with @{username} skipped ({exc.status}).')
                return
            raise
        if isinstance(data, list):
            rows, has_more = data, False
        elif isinstance(data, dict) and isinstance(data.get('list'), list):
            rows, has_more = data['list'], data.get('hasMore') is True
        else:
            emit('log', f'Chat with @{username} has an unexpected format; skipped.')
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


def _inbox_names(client: ApiClient, control: Control, emit: EventSink) -> list[str]:
    """Recent chat partners from the inbox, newest first.

    Only tiny identifiers are read; no message text is touched here.
    """
    names: list[str] = []
    seen: set[str] = set()
    offset = 0
    for _ in range(MAX_CHAT_PAGES):
        control.checkpoint()
        data = client.request('/chats', {'limit': MESSAGE_PAGE_SIZE, 'offset': offset,
                                         'order': 'recent'})
        rows = data.get('list') if isinstance(data, dict) else data
        if not isinstance(rows, list) or not rows:
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


def _list_rows(client: ApiClient, control: Control, emit: EventSink) -> list[dict[str, Any]]:
    """Users from the account's own custom lists (favorites and similar)."""
    list_ids: list[str] = []
    offset = 0
    for _ in range(4):
        control.checkpoint()
        data = client.request('/lists', {'limit': LIST_PAGE_SIZE, 'offset': offset})
        rows = data.get('list') if isinstance(data, dict) else data
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            list_id = str(row.get('id', ''))
            if list_id.isdigit() and list_id not in list_ids:
                list_ids.append(list_id)
        has_more = data.get('hasMore') if isinstance(data, dict) else None
        if has_more is False or len(list_ids) >= MAX_LISTS:
            break
        offset += len(rows)
        if has_more is None and len(rows) < LIST_PAGE_SIZE:
            break
    list_ids = list_ids[:MAX_LISTS]
    emit('offer_progress', {'phase': 'lists', 'done': 0, 'total': len(list_ids)})
    users: list[dict[str, Any]] = []
    seen: set[str] = set()
    for number, list_id in enumerate(list_ids, start=1):
        control.checkpoint()
        offset = 0
        for _ in range(8):
            data = client.request(f'/lists/{list_id}/users',
                                  {'limit': LIST_PAGE_SIZE, 'offset': offset})
            rows = data.get('list') if isinstance(data, dict) else data
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = _username(row.get('username'))
                if name and name not in seen:
                    seen.add(name)
                    users.append(row)
                    if len(users) >= MAX_LIST_USERS:
                        emit('offer_progress', {'phase': 'lists', 'done': number,
                                                'total': len(list_ids)})
                        return users
            has_more = data.get('hasMore') if isinstance(data, dict) else None
            if has_more is False:
                break
            offset += len(rows)
            if has_more is None and len(rows) < LIST_PAGE_SIZE:
                break
        emit('offer_progress', {'phase': 'lists', 'done': number, 'total': len(list_ids)})
    return users


def _promotion_offers(client: ApiClient, control: Control, emit: EventSink) -> list[Offer]:
    """Banners the website shows to this account under "promotions".

    Only the URL of each banner is followed: a shared trial link is used
    directly, a creator profile is classified like any other profile. Banner
    images and other creative content are not downloaded.
    """
    data = client.request('/users/promotions')
    rows = data.get('list') if isinstance(data, dict) and isinstance(data.get('list'), list) else data
    if not isinstance(rows, list):
        emit('log', 'Website promotions have an unexpected format; skipped.')
        return []
    emit('log', f'Website promotions: {len(rows)} banner(s) available.')
    result: list[Offer] = []
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
                result.append(Offer(key=f'trialcode:{OfferHistory.code_token(code)}',
                                    username=name, kind=TRIAL, source='website promotion',
                                    code=code,
                                    detail='Free trial link from a website promotion'))
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
        except ApiError as exc:
            emit('log', f'Promotion profile @{name} skipped ({exc.status}).')
            ignored += 1
            continue
        offer = classify(profile, source='website promotion',
                         status=SUBSCRIBED if profile.get('subscribedBy') is True else AVAILABLE)
        if offer is not None:
            result.append(offer)
        else:
            ignored += 1
        emit('offer_progress', {'phase': 'promotions', 'done': index, 'total': len(profiles)})
    if ignored:
        emit('log', f'Website promotions: {ignored} banner(s) ignored because they are paid, '
                    f'unreadable or not zero-price. Paid offers are never listed or claimed.')
    return result


def scan_offers(client: ApiClient, control: Control, *, include_active: bool = True,
                include_expired: bool = True, include_messages: bool = True,
                include_inbox: bool = True, include_lists: bool = True,
                include_promotions: bool = True,
                extra_profiles: Iterable[str] = (), history: OfferHistory | None = None,
                emit: EventSink = no_events) -> list[Offer]:
    """Build the current free/trial list for the signed-in account.

    Active subscriptions are included so already-subscribed pages appear as
    read-only rows; they can never be subscribed again from this tab.
    """
    history = history or OfferHistory.load()
    offers: dict[str, Offer] = {}
    chat_names: list[str] = []
    subscribed_names: set[str] = set()

    def add(offer: Offer | None) -> None:
        if offer is None:
            return
        if offer.kind == TRIAL and offer.code and history.is_claimed(offer.code):
            offer.status = USED
        if offer.kind == FREE and offer.user_id and history.is_subscribed(offer.user_id):
            offer.status = SUBSCRIBED
        previous = offers.get(offer.key)
        if previous is not None:
            # Active subscription state always wins over a chat/profile sighting.
            if previous.status == SUBSCRIBED:
                return
            if offer.status == SUBSCRIBED:
                offers[offer.key] = offer
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
                return
            if offer.actionable and not same_creator.actionable:
                # Prefer the row that can actually be claimed (a shared code).
                offers.pop(same_creator.key, None)
                offers[offer.key] = offer
            return
        offers[offer.key] = offer

    client.user('me')
    states: list[str] = []
    if include_active:
        states.append('active')
    if include_expired:
        states.append('expired')
    for number,state in enumerate(states, start=1):
        for row in _subscription_rows(client, control, state, emit):
            name = _username(row.get('username'))
            active = state == 'active' or row.get('subscribedBy') is True
            if active and name:
                subscribed_names.add(name)
                chat_names.append(name)
            add(classify(row, source=f'{state} subscription',
                         status=SUBSCRIBED if active else AVAILABLE))
        emit('offer_progress', {'phase': 'subscriptions', 'done': number, 'total': len(states)})

    if include_inbox:
        inbox = _inbox_names(client, control, emit)
        emit('log', f'Inbox: {len(inbox)} recent chat partner(s) found.')
        known = {offer.username.casefold() for offer in offers.values()}
        chat_names.extend(name for name in inbox if name not in subscribed_names)
        candidates = [name for name in inbox if name.casefold() not in known]
        if len(candidates) > MAX_INBOX_PROFILES:
            emit('log', f'Checking the first {MAX_INBOX_PROFILES} of {len(candidates)} inbox '
                        f'profiles; the rest are covered by the message scan only.')
            candidates = candidates[:MAX_INBOX_PROFILES]
        for index, name in enumerate(candidates, start=1):
            control.checkpoint()
            try:
                profile = client.user(name)
            except ApiError as exc:
                if exc.status in {401, 429}:
                    raise
                emit('log', f'Inbox profile @{name} skipped ({exc.status}).')
                continue
            add(classify(profile, source='inbox chat partner',
                         status=SUBSCRIBED if profile.get('subscribedBy') is True else AVAILABLE))
            emit('offer_progress', {'phase': 'profiles', 'done': index, 'total': len(candidates)})

    if include_lists:
        list_rows = _list_rows(client, control, emit)
        emit('log', f'Custom lists: {len(list_rows)} creator(s) found.')
        for row in list_rows:
            add(classify(row, source='custom list',
                         status=SUBSCRIBED if row.get('subscribedBy') is True else AVAILABLE))

    if include_promotions:
        for offer in _promotion_offers(client, control, emit):
            add(offer)

    if include_messages and chat_names:
        names = list(dict.fromkeys(chat_names))
        if len(names) > MAX_CHATS:
            emit('log', f'Checking the newest messages of the first {MAX_CHATS} of {len(names)} chats; '
                        f'the rest are skipped to keep the scan bounded.')
            names = names[:MAX_CHATS]
        else:
            emit('log', f'Checking the newest messages of {len(names)} chat(s) for trial links.')
        for index, name in enumerate(names, start=1):
            for offer in _chat_offers(client, control, name, emit):
                if offer.username in subscribed_names:
                    offer.status = SUBSCRIBED
                add(offer)
            emit('offer_progress', {'phase': 'messages', 'done': index, 'total': len(names)})

    extra_profiles = list(extra_profiles)
    for index, name in enumerate(extra_profiles, start=1):
        clean = _username(name)
        if not clean:
            emit('log', 'Ignored an invalid creator name in the check list.')
            continue
        try:
            profile = client.user(clean)
        except ApiError as exc:
            emit('log', f'Profile @{clean} could not be read ({exc.status}); skipped.')
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
    """Act on one offer after the user confirmed it. Returns a safe message."""
    if offer.status != AVAILABLE:
        raise AppError(f'@{offer.username} is already {offer.reason.lower()}. Nothing was sent.')
    if offer.kind == FREE:
        if not offer.user_id.isdigit():
            raise AppError('This free page has no usable account ID. Rescan and try again.')
        control.checkpoint()
        client.post(f'/users/{offer.user_id}/subscribe', {})
        offer.status = SUBSCRIBED
        (history or OfferHistory.load()).mark_subscribed(offer.user_id, offer.username, FREE)
        emit('log', f'Subscribed to free page @{offer.username}.')
        return f'Subscribed to @{offer.username} (free page).'
    if offer.kind != TRIAL:
        raise AppError('Unsupported offer type.')
    if not offer.code:
        raise AppError('This trial has no claimable code. Open it on the website instead.')
    control.checkpoint()
    client.post('/trials/claim', {'code': offer.code})
    offer.status = USED
    (history or OfferHistory.load()).mark_claimed(offer.code, offer.username)
    emit('log', f'Free trial claimed for @{offer.username}.')
    return f'Free trial claimed for @{offer.username}.'


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
