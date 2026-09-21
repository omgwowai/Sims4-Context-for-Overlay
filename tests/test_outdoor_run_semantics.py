"""New gameplay activities must retain execution facts and activity boundaries."""

import copy
import unittest

from support import ROOT
from context_overlay.experience.experience_policy import classify
from experience_view import build_experiences
from test_experience_view import action, audit, units
from test_filter_events import event, micro_fixture, time


class OutdoorRunSemanticTests(unittest.TestCase):
    def test_grill_chain_includes_product_but_serving_eating_and_dishes_stay_separate(self):
        rows = [action('cook', ('35079', 'grill_CreateFood'), 100, 200),
                action('stage', ('102689', 'grill_MakeRecipe_Pot_Staging_Basic'), 200, 600),
                action('finish', ('35278', 'grill_CreateFinalFood'), 610, 650),
                action('serve', ('13435', 'generic_food_Grab'), 660, 680),
                action('eat', ('13433', 'generic_consume_food'), 700, 900),
                action('wash', ('126860', 'dishwasher_LoadDishes_AfterEating'), 910, 950)]
        for parent, child in zip(rows, rows[1:]):
            child['facts']['parent_event_id'] = parent['event_id']
        product = event('product', 'crafting.completed', {'crafted_object': {'key': 'object:meal'}})
        product['cause'] = {'event_id': rows[2]['event_id']}
        result = build_experiences(rows + [product], 'run')
        roots = units(result, 'activities')
        self.assertEqual(len(roots), 4)
        cook = next(u for u in roots if u['action_tuning'] == 'grill_CreateFood')
        self.assertEqual((cook['started'], cook['ended']), (time(100), time(650)))
        self.assertEqual(cook['continuation_step_count'], 3)
        self.assertEqual(units(result, 'facts')[0]['activity'], cook['id'])
        for name in ('stage', 'finish'):
            self.assertEqual(audit(result, name)['units'], audit(result, 'cook')['units'])
        for name in ('serve', 'eat', 'wash'):
            self.assertNotEqual(audit(result, name)['units'], audit(result, 'cook')['units'])
        rows[1]['facts']['actor'] = {'key': 'sim:other'}
        unlinked = build_experiences(rows, 'run')
        self.assertEqual(units(unlinked, 'activities')[0]['ended'], time(200))
        self.assertTrue(audit(unlinked, 'stage')['units'])

    def test_selectors_keep_choices_without_claiming_execution(self):
        for identifier, name in (('38875', 'grill_StartCraftingAutonomously'),
                                 ('389838', 'pool-swim_Autonomous_Start'),
                                 ('100071', 'bed_Autonomous_DoubleBed_Nap'),
                                 ('100080', 'bed_Autonomous_SingleBed_Nap')):
            with self.subTest(name=name):
                selector = action('selector', (identifier, name), 100, 100)
                result = build_experiences([selector], 'run')
                self.assertFalse(units(result, 'activities'))
                choice = event('choice', 'autonomy.decision', {'selected': {'action': {
                    'id': identifier, 'tuning_name': name}}})
                self.assertEqual(classify(choice), 'action')
                self.assertEqual(classify(choice, 'future-version'), 'unknown')

    def test_swim_and_lounge_postures_do_not_invent_an_activity_or_extend_its_start(self):
        for root_resource, posture_resource in (
                (('105715', 'pool-swim_Autonomous'), ('102325', 'sim-swim')),
                (('209423', 'suntan_LoungeChair_Swimwear'), ('207637', 'generic_LoungeChair'))):
            root = action('root', root_resource, 200, 500)
            support = action('support', posture_resource, 100, 600)
            support['facts'].update(visible=True, is_super=True, trigger={'name': 'POSTURE_GRAPH'})
            result = build_experiences([root, support], 'run')
            activity, = units(result, 'activities')
            self.assertEqual((activity['started'], activity['ended']), (time(200), time(500)))
            self.assertNotEqual(audit(result, 'root')['units'], audit(result, 'support')['units'])
            self.assertTrue(audit(result, 'support')['units'])
            self.assertFalse(units(build_experiences([support], 'run'), 'activities'))

    def test_join_mentor_keeps_actor_and_uses_only_verified_provider_for_details(self):
        provider, child, decision = micro_fixture()
        provider['facts'].update(tuning_id='263924', tuning_name='[Join]GroupCooking_Mentor')
        child['facts'].update(tuning_id='13550', tuning_name='mentor_Mixer_Food')
        decision['payload']['selected']['action'] = {'id': '13550', 'tuning_name': 'mentor_Mixer_Food'}
        candidate = decision['payload']['stages'][0]['candidates'][0]
        candidate['action'] = {'id': '263924', 'tuning_name': '[Join]GroupCooking_Mentor'}
        result = build_experiences([provider, child, decision], 'run')
        root, = units(result, 'activities')
        self.assertEqual(root['roles'], provider['facts'].get('roles') or [])
        self.assertEqual(audit(result, 'provider')['units'], audit(result, 'micro')['units'])
        child['facts']['actor'] = {'key': 'sim:2'}
        result = build_experiences([provider, child, decision], 'run')
        self.assertNotEqual(audit(result, 'provider')['units'], audit(result, 'micro')['units'])
        for name in ('[Join]GroupCooking_Mentor_Modded', '[Proxy]GroupCooking_Mentor'):
            provider['facts']['tuning_name'] = name
            self.assertEqual(classify(provider), 'unknown')

    def test_new_social_attempt_and_movie_reaction_keep_observed_outcomes(self):
        social = action('social', ('25670', 'mixer_socials_TellJoke_group_Funny_alwaysOn'), 100, 200)
        social['started_time'] = None
        social['facts']['finishing_type'] = 'INTERACTION_INCOMPATIBILITY'
        reaction = action('reaction', ('129263', 'reactions_Movie_Fallback_PlotTwist'), 300, 300)
        effect = event('effect', 'relationship.knowledge', {'after': {'skill': 'comedy'}})
        effect['cause'] = {'event_id': reaction['event_id']}
        rows = [social, reaction, effect]
        before = copy.deepcopy(rows)
        result = build_experiences(rows, 'run')
        root, = units(result, 'activities')
        self.assertIsNone(root['started'])
        self.assertEqual(root['exit'], 'INTERACTION_INCOMPATIBILITY')
        self.assertTrue(audit(result, 'reaction')['units'])
        self.assertEqual(len(units(result, 'facts')), 1)
        self.assertEqual(rows, before)


if __name__ == '__main__':
    unittest.main()
