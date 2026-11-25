import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ofdl.api import ApiError
from ofdl.common import AppError, Cancelled, Control
from ofdl.offers import (AVAILABLE, FREE, SUBSCRIBED, TRIAL, USED, Offer, OfferHistory,
                         claim_offer, classify, scan_offers, trial_links)

FREE_ROW = {'id': 11, 'username': 'freebie', 'subscribePrice': 0, 'subscribedBy': False}
TRIAL_ROW = {'id': 22, 'username': 'tester', 'subscribePrice': 12.5,
             'currentSubscribePrice': 0}
PAID_ROW = {'id': 33, 'username': 'paidpage', 'subscribePrice': 12.5,
            'currentSubscribePrice': 12.5}
PROMO_ROW = {'id': 44, 'username': 'promo', 'subscribePrice': 9,
             'promotions': [{'price': 0, 'canClaim': True, 'subscribeDays': 7},
                            {'price': 3, 'canClaim': True}]}


class FakeClient:
    def __init__(self, *, subscriptions=None, chats=None, profiles=None, inbox=None,
                 lists=None, list_users=None, promotions=None):
        self.subscriptions = subscriptions or {}
        self.chats = chats or {}
        self.profiles = profiles or {}
        self.inbox = inbox or {'list': [], 'hasMore': False}
        self.lists = lists or {'list': [], 'hasMore': False}
        self.list_users = list_users or {}
        self.promotions = promotions if promotions is not None else []
        self.posts = []

    def user(self, name='me'):
        return self.profiles.get(name, {'id': 1, 'username': name})

    def paginate(self, endpoint, kind, *, since=None, subscription_type='active'):
        for row in self.subscriptions.get(subscription_type, []):
            yield row

    def request(self, endpoint, params=None):
        if endpoint == '/users/promotions':
            return self.promotions
        if endpoint == '/chats':
            return self.inbox
        if endpoint == '/lists':
            return self.lists
        if endpoint.startswith('/lists/'):
            list_id = endpoint.split('/')[2]
            return self.list_users.get(list_id, {'list': [], 'hasMore': False})
        name = endpoint.split('/')[2]
        return self.chats.get(name, {'list': [], 'hasMore': False})

    def post(self, endpoint, payload=None):
        self.posts.append((endpoint, dict(payload or {})))
        return {'success': True}


class TrialLinkTests(unittest.TestCase):
    def test_trial_links_are_found_in_html_and_deduplicated(self):
        text = ('Hi <a href="https://onlyfans.com/cutie/trial/AbC_123">onlyfans.com/cutie/…</a> '
                'again https://onlyfans.com/cutie/trial/AbC_123 and '
                'https://onlyfans.com/other/trial/zzz-9')
        self.assertEqual(trial_links(text), [('cutie', 'AbC_123'), ('other', 'zzz-9')])

    def test_non_trial_links_are_ignored(self):
        self.assertEqual(trial_links('https://onlyfans.com/cutie and https://example.com/x/trial/1'), [])


class ClassificationTests(unittest.TestCase):
    def test_free_page_is_detected(self):
        offer = classify(FREE_ROW, source='test')
        self.assertEqual(offer.kind, FREE)
        self.assertEqual(offer.status, AVAILABLE)
        self.assertTrue(offer.available)
        self.assertTrue(offer.actionable)
        self.assertEqual(offer.key, 'free:11')

    def test_free_trial_current_price_is_detected(self):
        offer = classify(TRIAL_ROW, source='test')
        self.assertEqual(offer.kind, TRIAL)
        self.assertEqual(offer.regular_price, 12.5)
        self.assertFalse(offer.actionable, 'a trial without a code is website-only')
        self.assertIn('website', offer.reason)

    def test_free_trial_promotion_is_detected_with_duration(self):
        offer = classify(PROMO_ROW, source='test')
        self.assertEqual(offer.kind, TRIAL)
        self.assertIn('7 days', offer.detail)

    def test_paid_offer_is_never_returned(self):
        self.assertIsNone(classify(PAID_ROW, source='test'))
        self.assertIsNone(classify({'id': 5, 'username': 'x', 'subscribePrice': 4.99}, source='test'))

    def test_current_profile_price_wins_over_old_subscription_price(self):
        profile = dict(FREE_ROW, subscribePrice=15, currentSubscribePrice=15,
                       subscribedByData={'regularPrice':0})
        self.assertIsNone(classify(profile, source='checked profile'))

    def test_subscribed_state_is_preserved(self):
        offer = classify(FREE_ROW, source='test', status=SUBSCRIBED)
        self.assertEqual(offer.status, SUBSCRIBED)
        self.assertFalse(offer.actionable)

    def test_rows_without_usable_identity_are_skipped(self):
        self.assertIsNone(classify({'username': 'nobody'}, source='test'))
        self.assertIsNone(classify({'id': 'abc', 'username': 'nobody'}, source='test'))
        self.assertIsNone(classify({'id': 1, 'username': '../bad'}, source='test'))


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'free_trials.json'

    def test_codes_are_stored_hashed_and_survive_a_reload(self):
        history = OfferHistory(self.path)
        history.mark_claimed('secret-code', 'cutie')
        history.mark_subscribed('11', 'freebie', FREE)
        raw = self.path.read_text(encoding='utf-8')
        self.assertNotIn('secret-code', raw)
        self.assertIn('freebie', raw)
        reloaded = OfferHistory.load(self.path)
        self.assertTrue(reloaded.is_claimed('secret-code'))
        self.assertTrue(reloaded.is_subscribed('11'))
        self.assertFalse(reloaded.is_claimed('other-code'))

    def test_corrupt_history_is_ignored_instead_of_crashing(self):
        self.path.write_text('{not json', encoding='utf-8')
        history = OfferHistory.load(self.path)
        self.assertFalse(history.is_claimed('anything'))
        self.assertEqual(history.claimed, [])

    def test_duplicate_marks_are_not_appended(self):
        history = OfferHistory(self.path)
        history.mark_claimed('code', 'cutie')
        history.mark_claimed('code', 'cutie')
        history.mark_subscribed('11', 'freebie')
        history.mark_subscribed('11', 'freebie')
        values = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(len(values['claimed']), 1)
        self.assertEqual(len(values['subscribed']), 1)


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.history = OfferHistory(Path(self.temp.name) / 'free_trials.json')
        self.control = Control()

    def test_free_page_is_subscribed_and_recorded(self):
        client = FakeClient(profiles={'freebie': FREE_ROW})
        offer = classify(FREE_ROW, source='test')
        message = claim_offer(client, offer, self.control, self.history)
        self.assertIn('Subscribed', message)
        self.assertEqual(client.posts, [('/users/11/subscribe', {})])
        self.assertEqual(offer.status, SUBSCRIBED)
        self.assertTrue(self.history.is_subscribed('11'))

    def test_trial_code_is_claimed_without_a_price(self):
        client = FakeClient(profiles={'cutie': {'id': 22, 'username': 'cutie', 'subscribePrice': 12.5}})
        offer = Offer(key='trialcode:1', username='cutie', kind=TRIAL, source='chat',
                      detail='link', code='abc', user_id='22')
        message = claim_offer(client, offer, self.control, self.history)
        self.assertIn('trial claimed', message.lower())
        self.assertEqual(client.posts, [('/trials/claim', {'code': 'abc'})])
        self.assertEqual(offer.status, SUBSCRIBED)
        self.assertTrue(self.history.is_claimed('abc'))
        self.assertTrue(self.history.is_subscribed('22'))

    def test_duplicate_trial_code_is_not_sent_twice(self):
        client = FakeClient(profiles={'cutie': {'id': 22, 'username': 'cutie', 'subscribePrice': 12.5}})
        self.history.mark_claimed('abc', 'cutie')
        offer = Offer(key='trialcode:1', username='cutie', kind=TRIAL, source='chat',
                      detail='link', code='abc', user_id='22')
        with self.assertRaisesRegex(AppError, 'already claimed'):
            claim_offer(client, offer, self.control, self.history)
        self.assertEqual(client.posts, [])

    def test_price_change_after_scan_never_sends_subscription(self):
        offer = classify(FREE_ROW, source='test')
        client = FakeClient(profiles={'freebie': dict(FREE_ROW, subscribePrice=15)})
        with self.assertRaises(AppError):
            claim_offer(client, offer, self.control, self.history)
        self.assertEqual(client.posts, [])

    def test_trial_without_code_is_refused(self):
        client = FakeClient()
        offer = classify(TRIAL_ROW, source='test')
        with self.assertRaises(AppError):
            claim_offer(client, offer, self.control, self.history)
        self.assertEqual(client.posts, [])

    def test_already_subscribed_offer_is_refused(self):
        client = FakeClient()
        offer = classify(FREE_ROW, source='test', status=SUBSCRIBED)
        with self.assertRaises(AppError):
            claim_offer(client, offer, self.control, self.history)
        self.assertEqual(client.posts, [])

    def test_server_side_subscription_during_claim_is_refused(self):
        offer = classify(FREE_ROW, source='test')
        client = FakeClient(profiles={'freebie': dict(FREE_ROW, subscribedBy=True)})
        with self.assertRaisesRegex(AppError, 'already subscribed'):
            claim_offer(client, offer, self.control, self.history)
        self.assertEqual(client.posts, [])
        self.assertEqual(offer.status, SUBSCRIBED)


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.history = OfferHistory(Path(self.temp.name) / 'free_trials.json')
        self.control = Control()
        self.logs = []
        self.events = []

    def emit(self, kind, value):
        self.events.append((kind, value))
        if kind == 'log':
            self.logs.append(value)

    def test_scan_marks_active_rows_read_only_and_dedupes_chat_links(self):
        client = FakeClient(
            subscriptions={
                'active': [dict(FREE_ROW, subscribedBy=True)],
                'expired': [dict(TRIAL_ROW, subscribedByData={'regularPrice': 12.5})],
            },
            chats={'freebie': {'list': [
                {'id': 2, 'text': '<a href="https://onlyfans.com/freebie/trial/same">x</a>'},
                {'id': 1, 'text': 'https://onlyfans.com/cutie/trial/new-code'},
            ], 'hasMore': False}},
        )
        offers = scan_offers(client, self.control, include_messages=True,
                             history=self.history, emit=self.emit)
        by_key = {offer.key: offer for offer in offers}
        self.assertEqual(by_key['free:11'].status, SUBSCRIBED)
        self.assertFalse(by_key['free:11'].actionable)
        chat_trial = [offer for offer in offers if offer.code == 'new-code'][0]
        self.assertEqual(chat_trial.username, 'cutie')
        self.assertTrue(chat_trial.available)
        self.assertTrue(chat_trial.actionable)
        trial_from_sub = [offer for offer in offers if offer.key == 'trial:22'][0]
        self.assertEqual(trial_from_sub.kind, TRIAL)

    def test_claimed_trial_link_is_marked_used_on_the_next_scan(self):
        self.history.mark_claimed('used-code', 'cutie')
        client = FakeClient(chats={'freebie': {'list': [
            {'id': 1, 'text': 'https://onlyfans.com/cutie/trial/used-code'}], 'hasMore': False}})
        # Active subscription keeps the chat scan enabled.
        client.subscriptions = {'active': [dict(FREE_ROW, subscribedBy=True)]}
        offers = scan_offers(client, self.control, include_active=True, include_expired=False,
                             history=self.history, emit=self.emit)
        used = [offer for offer in offers if offer.code == 'used-code'][0]
        self.assertEqual(used.status, USED)
        self.assertFalse(used.actionable)

    def test_expired_creator_chat_is_scanned_for_new_trial_links(self):
        client = FakeClient(
            subscriptions={'expired': [dict(PAID_ROW, username='lapsed')]},
            chats={'lapsed': {'list': [
                {'id': 1, 'text': 'https://onlyfans.com/lapsed/trial/come-back'}],
                'hasMore': False}})
        offers = scan_offers(client, self.control, include_active=False, include_expired=True,
                             include_inbox=False, include_lists=False, include_promotions=False,
                             history=self.history, emit=self.emit)
        links = [offer for offer in offers if offer.code == 'come-back']
        self.assertEqual(len(links), 1)
        self.assertTrue(links[0].actionable)

    def test_deep_scan_uses_more_chat_pages(self):
        calls = []
        class Pages(FakeClient):
            def request(self, endpoint, params=None):
                calls.append(params)
                if len(calls) < 5:
                    return {'list': [{'id': 100-len(calls), 'text': 'no link'}], 'hasMore': True}
                return {'list': [{'id': 1, 'text': 'https://onlyfans.com/late/trial/deep-code'}],
                        'hasMore': False}
        client = Pages(subscriptions={'active': [dict(FREE_ROW, subscribedBy=True)]})
        offers = scan_offers(client, self.control, include_active=True, include_expired=False,
                             include_inbox=False, include_lists=False, include_promotions=False,
                             deep=True, history=self.history, emit=self.emit)
        self.assertTrue(any(offer.code == 'deep-code' for offer in offers))
        self.assertEqual(len(calls), 5)

    def test_checked_profiles_use_the_same_rules(self):
        client = FakeClient(profiles={'freebie': FREE_ROW, 'paidpage': PAID_ROW})
        offers = scan_offers(client, self.control, include_active=False, include_expired=False,
                             include_messages=False, extra_profiles=['freebie', 'paidpage', 'bad/name'],
                             history=self.history, emit=self.emit)
        self.assertEqual([offer.kind for offer in offers], [FREE])
        self.assertTrue(any('invalid creator name' in line for line in self.logs))

    def test_inbox_partner_trial_link_is_found_without_a_subscription(self):
        paid = {'id': 99, 'username': 'stranger', 'subscribePrice': 10, 'currentSubscribePrice': 10}
        client = FakeClient(
            inbox={'list': [{'id': 1, 'withUser': {'id': 99, 'username': 'stranger'}}],
                   'hasMore': False},
            profiles={'stranger': paid},
            chats={'stranger': {'list': [
                {'id': 1, 'text': 'https://onlyfans.com/stranger/trial/inbox-code'}],
                'hasMore': False}})
        offers = scan_offers(client, self.control, include_active=False, include_expired=False,
                             include_inbox=True, include_lists=False, include_messages=True,
                             history=self.history, emit=self.emit)
        stranger = [offer for offer in offers if offer.username == 'stranger']
        self.assertEqual(len(stranger), 1)
        self.assertEqual(stranger[0].code, 'inbox-code')
        self.assertTrue(stranger[0].available)

    def test_inbox_free_page_is_classified(self):
        client = FakeClient(
            inbox={'list': [{'id': 1, 'withUser': {'id': 99, 'username': 'stranger'}}],
                   'hasMore': False},
            profiles={'stranger': {'id': 99, 'username': 'stranger', 'subscribePrice': 0}})
        offers = scan_offers(client, self.control, include_active=False, include_expired=False,
                             include_inbox=True, include_lists=False, include_messages=False,
                             history=self.history, emit=self.emit)
        self.assertEqual([offer.kind for offer in offers], [FREE])
        self.assertTrue(offers[0].available)

    def test_custom_list_members_are_checked(self):
        listed = {'id': 77, 'username': 'listed', 'subscribePrice': 0}
        client = FakeClient(lists={'list': [{'id': 7}], 'hasMore': False},
                            list_users={'7': {'list': [listed], 'hasMore': False}})
        offers = scan_offers(client, self.control, include_active=False, include_expired=False,
                             include_inbox=False, include_messages=False, include_lists=True,
                             history=self.history, emit=self.emit)
        self.assertEqual([offer.username for offer in offers], ['listed'])

    def test_website_promotion_banners_are_followed(self):
        client = FakeClient(
            promotions=[{'imageSrc': 'https://cdn.example/a.jpg',
                         'url': 'https://onlyfans.com/promo-free'},
                        {'imageSrc': 'https://cdn.example/b.jpg',
                         'url': 'https://onlyfans.com/cutie/trial/banner-code'},
                        {'imageSrc': 'https://cdn.example/c.jpg',
                         'url': 'https://evil.example/phish'}],
            profiles={'promo-free': {'id': 55, 'username': 'promo-free', 'subscribePrice': 0}})
        offers = scan_offers(client, self.control, include_active=False, include_expired=False,
                             include_inbox=False, include_lists=False, include_messages=False,
                             include_promotions=True, history=self.history, emit=self.emit)
        by_name = {offer.username: offer for offer in offers}
        self.assertEqual(by_name['promo-free'].kind, FREE)
        self.assertEqual(by_name['promo-free'].source, 'website promotion')
        self.assertEqual(by_name['cutie'].code, 'banner-code')
        self.assertEqual(len(offers), 2)

    def test_paid_promo_banner_never_becomes_an_offer_or_action(self):
        client = FakeClient(
            promotions=[{'imageSrc':'https://cdn.example/x.jpg',
                         'url':'https://onlyfans.com/paidpage'}],
            profiles={'paidpage': {'id': 33, 'username': 'paidpage',
                                   'subscribePrice': 12.5, 'currentSubscribePrice': 12.5}})
        offers = scan_offers(client, self.control, include_active=False, include_expired=False,
                             include_inbox=False, include_lists=False, include_messages=False,
                             include_promotions=True, history=self.history, emit=self.emit)
        self.assertEqual(offers, [])
        self.assertEqual(client.posts, [])

    def test_scan_emits_phase_progress_for_the_ui(self):
        client = FakeClient(
            subscriptions={'active': [dict(FREE_ROW, subscribedBy=True)]},
            inbox={'list': [{'id': 1, 'withUser': {'id': 99, 'username': 'stranger'}}],
                   'hasMore': False},
            lists={'list': [{'id': 7}], 'hasMore': False},
            list_users={'7': {'list': [{'id': 77, 'username': 'listed', 'subscribePrice': 0}],
                              'hasMore': False}})
        scan_offers(client, self.control, history=self.history, emit=self.emit)
        phases = {value.get('phase') for kind, value in self.events if kind == 'offer_progress'}
        self.assertTrue({'subscriptions', 'inbox', 'lists', 'profiles', 'messages'} <= phases)

    def test_shared_code_is_preferred_over_a_codeless_trial_row(self):
        client = FakeClient(
            subscriptions={'active': [dict(FREE_ROW, subscribedBy=True)],
                           'expired': [dict(TRIAL_ROW, subscribedByData={'regularPrice': 12.5})]},
            chats={'freebie': {'list': [
                {'id': 1, 'text': 'https://onlyfans.com/tester/trial/shared-code'}], 'hasMore': False}})
        offers = scan_offers(client, self.control, include_messages=True,
                             history=self.history, emit=self.emit)
        tester = [offer for offer in offers if offer.username == 'tester']
        self.assertEqual(len(tester), 1)
        self.assertEqual(tester[0].code, 'shared-code')
        self.assertTrue(tester[0].available)

    def test_missing_chat_is_skipped_without_failing_the_scan(self):
        client = FakeClient(subscriptions={'active': [dict(FREE_ROW, subscribedBy=True)]})
        def broken(endpoint, params=None):
            from ofdl.api import ApiError
            raise ApiError(404)
        client.request = broken
        offers = scan_offers(client, self.control, include_inbox=False, include_lists=False,
                             include_promotions=False, history=self.history, emit=self.emit)
        self.assertEqual(len(offers), 1)
        self.assertTrue(any('incomplete source' in line for line in self.logs))

    def test_optional_endpoint_errors_preserve_results_and_continue_other_sources(self):
        for endpoint in ('/chats', '/lists', '/users/promotions'):
            with self.subTest(endpoint=endpoint):
                client = FakeClient(subscriptions={'active':[FREE_ROW]},
                                    profiles={'extra':dict(FREE_ROW, id=99, username='extra')})
                request = client.request
                def failing(route, params=None):
                    if route == endpoint:
                        raise ApiError(404)
                    return request(route, params)
                self.events.clear()
                with patch.object(client, 'request', side_effect=failing):
                    offers = scan_offers(client, self.control, include_messages=False,
                                         extra_profiles=['extra'], history=self.history, emit=self.emit)
                self.assertEqual({offer.username for offer in offers}, {'freebie','extra'})
                self.assertTrue(any(kind == 'offer_warning' for kind, _ in self.events))

    def test_later_list_page_failure_retains_rows_and_checks_next_list(self):
        client = FakeClient(lists={'list':[{'id':7},{'id':8}], 'hasMore':False})
        request = client.request
        def pages(route, params=None):
            if route == '/lists/7/users':
                if params['offset']:
                    raise ApiError(404)
                return {'list':[FREE_ROW], 'hasMore':True}
            if route == '/lists/8/users':
                return {'list':[dict(FREE_ROW, id=99, username='other')], 'hasMore':False}
            return request(route, params)
        with patch.object(client, 'request', side_effect=pages):
            offers = scan_offers(client, self.control, history=self.history, emit=self.emit)
        self.assertEqual({offer.username for offer in offers}, {'freebie','other'})
        self.assertTrue(any(kind == 'offer_warning' for kind, _ in self.events))

    def test_malformed_optional_responses_are_reported_not_treated_as_empty_success(self):
        for response in (None, {'unexpected':'format'}, 'not a list'):
            with self.subTest(response=response):
                client = FakeClient(subscriptions={'active':[FREE_ROW]})
                self.events.clear()
                with patch.object(client, 'request', return_value=response):
                    offers = scan_offers(client, self.control, include_messages=False,
                                         history=self.history, emit=self.emit)
                self.assertEqual([offer.username for offer in offers], ['freebie'])
                self.assertEqual(sum(kind == 'offer_warning' for kind, _ in self.events), 3)

    def test_auth_and_rate_limit_errors_stop_with_partial_rows_already_emitted(self):
        for status in (401, 429):
            with self.subTest(status=status):
                client = FakeClient(subscriptions={'active':[FREE_ROW]})
                self.events.clear()
                with patch.object(client, 'request', side_effect=ApiError(status)):
                    with self.assertRaises(ApiError):
                        scan_offers(client, self.control, history=self.history, emit=self.emit)
                partial = [value['offer'] for kind, value in self.events if kind == 'offer_row']
                self.assertEqual([offer.username for offer in partial], ['freebie'])

    def test_cancelled_scan_publishes_current_rows_before_cancellation(self):
        client = FakeClient(subscriptions={'active':[FREE_ROW]}, profiles={'extra':FREE_ROW})
        def cancel_on_row(kind, value):
            self.emit(kind, value)
            if kind == 'offer_row':
                self.control.stop()
        with self.assertRaises(Cancelled):
            scan_offers(client, self.control, extra_profiles=['extra'],
                        history=self.history, emit=cancel_on_row)
        partial = [value['offer'] for kind, value in self.events if kind == 'offer_row']
        self.assertEqual([offer.username for offer in partial], ['freebie'])

    def test_emitted_rows_are_snapshots_when_later_source_marks_subscribed(self):
        client = FakeClient(subscriptions={'expired':[FREE_ROW]},
                            lists={'list':[{'id':7}], 'hasMore':False},
                            list_users={'7':{'list':[dict(FREE_ROW, subscribedBy=True)], 'hasMore':False}})
        offers = scan_offers(client, self.control, history=self.history, emit=self.emit)
        partial = [value['offer'] for kind, value in self.events if kind == 'offer_row']
        self.assertEqual(partial[0].status, AVAILABLE)
        self.assertEqual(partial[-1].status, SUBSCRIBED)
        self.assertEqual(offers[0].status, SUBSCRIBED)


if __name__ == '__main__':
    unittest.main()
