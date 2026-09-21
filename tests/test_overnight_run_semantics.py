"""Anonymized overnight cases and counterexamples to unsafe experience merging."""

import copy
import unittest

from support import ROOT
from context_overlay.experience.experience_policy import classify
from experience_view import build_experiences
from test_experience_view import action, audit, units
from test_filter_events import event, micro_fixture, time


def social_pair(prefix='left', owner='sim:1', other='sim:2'):
    parent = action(prefix, ('13991', 'sim_BeAffectionate'), 100, 500)
    child = action(prefix + '_additional', ('13998', 'sim_Chat'), 100, 500)
    for row in (parent, child):
        row['entities'] = [owner, other]
        row['facts'].update(actor={'key': owner}, target={'key': other}, visible=True,
                            is_super=True, trigger={'name': 'AUTONOMY'}, name='聊天')
        row['facts']['roles'] = [{'entity_key': owner, 'role': 'actor'}, {'entity_key': other, 'role': 'target'}]
    parent['first_observed_time'] = time(50)
    parent['observations'] = [{'phase': 'queued', 'game_time': time(50)}]
    child['observations'] = [{'phase': 'queued', 'game_time': time(100)},
                             {'phase': 'started', 'game_time': time(100)},
                             {'phase': 'exited', 'game_time': time(500)},
                             {'phase': 'exited', 'game_time': time(500)}]
    return parent, child


class OvernightRunSemanticTests(unittest.TestCase):
    def test_additional_chat_folds_per_direction_and_retains_queue_effects_and_evidence(self):
        left, extra_left = social_pair()
        right, extra_right = social_pair('right', 'sim:2', 'sim:1')
        effect = event('effect', 'payment.completed', {'actual_amount': -3})
        effect['cause'] = {'event_id': extra_left['event_id']}
        rows = [left, extra_left, right, extra_right, effect]
        original = copy.deepcopy(rows)
        result = build_experiences(rows, 'run', 'sim:1')
        roots = units(result, 'activities')
        self.assertEqual(len(roots), 2)
        self.assertEqual({r['roles'][0]['entity_key'] for r in roots}, {'sim:1', 'sim:2'})
        for root in roots:
            self.assertEqual(root['action_tuning'], 'sim_BeAffectionate')
            self.assertEqual(root['queued'], time(50))
            self.assertEqual(root['started'], time(100))
            self.assertEqual(root['ended'], time(500))
            self.assertEqual(root['continuation_step_count'], 1)
            self.assertEqual(root['step_count'], 2)
        left_units = audit(result, 'left')['units']
        self.assertEqual(left_units, audit(result, 'left_additional')['units'])
        self.assertNotEqual(left_units, audit(result, 'right')['units'])
        self.assertEqual(units(result, 'facts')[0]['activity'], left_units[0])
        self.assertEqual(len([link for link in result['audit']['links']
                              if link['basis'] == 'reviewed_additional_social_same_execution' and link['merged']]), 2)
        self.assertEqual(rows, original)

    def test_a_topic_of_the_additional_chat_follows_the_primary_activity(self):
        parent, additional = social_pair()
        _, topic, decision = micro_fixture()
        topic['facts'].update(tuning_id='181932', tuning_name='socials_Targeted_Friendly_HolidaySocials_BellyLaugh',
                              target={'key': 'sim:2'})
        topic['started_time'], topic['ended_time'] = time(200), time(300)
        topic['facts']['roles'] = additional['facts']['roles']
        topic['entities'] = ['sim:1', 'sim:2']
        decision['payload']['selected']['action'] = {'id': '181932', 'tuning_name': topic['facts']['tuning_name']}
        candidate = decision['payload']['stages'][0]['candidates'][0]
        candidate.update(interaction_id=additional['facts']['interaction_id'],
                         action={'id': '13998', 'tuning_name': 'sim_Chat'}, target={'key': 'sim:2'})
        result = build_experiences([parent, additional, topic, decision], 'run')
        root, = units(result, 'activities')
        self.assertEqual(root['action_tuning'], 'sim_BeAffectionate')
        self.assertEqual(len(root['topics']), 1)
        self.assertEqual(root['ended'], time(500))
        self.assertEqual(audit(result, 'left')['units'], audit(result, 'micro')['units'])

    def test_open_overlapping_or_different_actor_chat_is_not_folded(self):
        scenarios = [('started_time', time(101)), ('ended_time', time(501)), ('ended_time', None),
                     ('started_time', None), ('first_observed_time', time(99)), ('zone_visit', 2), ('stage', 'started')]
        for field, value in scenarios:
            with self.subTest(field=field, value=value):
                parent, child = social_pair()
                child[field] = value
                result = build_experiences([parent, child], 'run')
                self.assertEqual(len(units(result, 'activities')), 2)
        for field, value in (('actor', {'key': 'sim:3'}), ('target', {'key': 'sim:3'}),
                             ('is_super', False), ('visible', False), ('finishing_type', 'RESET'),
                             ('trigger', {'name': 'PIE_MENU'}), ('outcome_result', 'FAILURE')):
            with self.subTest(field=field, value=value):
                parent, child = social_pair()
                child['facts'][field] = value
                result = build_experiences([parent, child], 'run')
                self.assertFalse(any(link['merged'] for link in result['audit']['links']))

    def test_independent_queue_choice_role_or_parent_never_masquerades_as_additional_chat(self):
        scenarios = [({'parent_event_id': 'run:another'}, None),
                     ({'parent_interaction_id': 'another'}, None),
                     ({'parent_actor_id': '3'}, None),
                     ({'decision_event_id': 'run:own_choice'}, None),
                     ({'roles': [{'entity_key': 'sim:3', 'role': 'participant'}]}, None),
                     ({}, [{'phase': 'queued', 'game_time': time(99)}]),
                     ({}, [{'phase': 'queued', 'game_time': time(101)}]),
                     ({}, [{'phase': 'queued', 'game_time': None}])]
        for changes, observations in scenarios:
            with self.subTest(changes=changes, observations=observations):
                parent, child = social_pair()
                child['facts'].update(changes)
                if observations is not None:
                    child['observations'] = observations
                result = build_experiences([parent, child], 'run')
                self.assertEqual(len(units(result, 'activities')), 2)
                self.assertNotEqual(audit(result, 'left')['units'], audit(result, 'left_additional')['units'])

    def test_agreeing_recorded_parent_is_allowed_but_different_outcomes_are_not(self):
        parent, child = social_pair()
        child['facts'].update(parent_event_id=parent['event_id'], parent_actor_id='1',
                              parent_interaction_id=parent['facts']['interaction_id'])
        result = build_experiences([parent, child], 'run')
        self.assertEqual(len(units(result, 'activities')), 1)
        child['outcome'] = 'different_outcome'
        result = build_experiences([parent, child], 'run')
        self.assertEqual(len(units(result, 'activities')), 2)

    def test_ambiguous_pairs_and_unreviewed_identity_or_version_remain_separate(self):
        for duplicate_parent in (False, True):
            parent, child = social_pair()
            duplicate = copy.deepcopy(parent if duplicate_parent else child)
            duplicate['event_id'] = 'run:duplicate'
            duplicate['facts']['interaction_id'] = 'duplicate'
            result = build_experiences([parent, child, duplicate], 'run')
            self.assertEqual(len(units(result, 'activities')), 3)
            self.assertFalse(any(link['merged'] for link in result['audit']['links']))
        parent, child = social_pair()
        child['facts']['tuning_name'] += '_Modded'
        result = build_experiences([parent, child], 'run')
        self.assertEqual(len(result['review']), 1)
        self.assertFalse(any(link['merged'] for link in result['audit']['links']))
        parent, child = social_pair()
        result = build_experiences([parent, child], 'run', game_version='future-version')
        self.assertEqual(len(result['review']), 2)
        self.assertFalse(any(link['merged'] for link in result['audit']['links']))
        standalone = build_experiences([child], 'run')
        self.assertEqual(len(units(standalone, 'activities')), 1)

    def test_gourmet_stage_extends_cooking_and_keeps_eating_separate(self):
        root = action('cook', ('13395', 'fridge_CreateTray'), 100, 200)
        transition = action('transition', ('13276', 'counter_Ico_Transition'), 200, 300)
        stage = action('gourmet', ('37780', 'counter_MakeFood_Staging_Gourmet'), 300, 600)
        meal = action('eat', ('13433', 'generic_consume_food'), 650, 900)
        for child, parent in ((transition, root), (stage, transition), (meal, stage)):
            child['facts']['parent_event_id'] = parent['event_id']
        product = event('product', 'crafting.completed', {'crafted_object': {'key': 'object:meal'}})
        product['cause'] = {'event_id': stage['event_id']}
        result = build_experiences([meal, product, stage, root, transition], 'run')
        self.assertEqual(len(units(result, 'activities')), 2)
        cooking = next(u for u in units(result, 'activities') if u['action_tuning'] == 'fridge_CreateTray')
        self.assertEqual(cooking['started'], time(100))
        self.assertEqual(cooking['ended'], time(600))
        self.assertEqual(cooking['continuation_step_count'], 3)
        self.assertEqual(units(result, 'facts')[0]['activity'], cooking['id'])
        self.assertEqual(audit(result, 'cook')['units'], audit(result, 'gourmet')['units'])
        self.assertNotEqual(audit(result, 'cook')['units'], audit(result, 'eat')['units'])
        stage['facts'].pop('parent_event_id')
        unlinked = build_experiences([root, transition, stage], 'run')
        self.assertEqual(units(unlinked, 'activities')[0]['ended'], time(300))
        self.assertTrue(any(u['action_tuning'] == 'counter_MakeFood_Staging_Gourmet' for u in unlinked['details']))

    def test_reviewed_movie_family_and_socials_preserve_execution_boundaries(self):
        names = ('DiamondsAreForSims', 'TheAdventuresOfSpaceshipSimulation', 'SimsOfTheDead', 'MoonlightMassacre',
                 'TheKhlumzeeSisters', 'Comedy2', 'RoaringHeights', 'TheSpiral', 'LostDogsJourneyHome', 'SuperHeroes')
        for index, name in enumerate(names, 128720):
            row = action('movie', (str(index), 'movie_Watch_' + name))
            self.assertEqual(classify(row), 'action')
            row['facts']['tuning_name'] += '_Similar'
            self.assertEqual(classify(row), 'unknown')
        attempted = action('throw', ('25887', 'mixer_social_ThrowDrink_targeted_mean_emotionSpecific'), None, 500)
        attempted['started_time'] = None
        self.assertEqual(classify(attempted), 'social_content')
        self.assertIsNone(units(build_experiences([attempted], 'run'), 'activities')[0]['started'])

    def test_recipe_selector_keeps_its_autonomy_choice_without_claiming_a_cooked_meal(self):
        selector = action('selector', ('13390', 'fridge_CookGourmetAutonomously'), 100, 100)
        self.assertEqual(classify(selector), 'router')
        self.assertFalse(units(build_experiences([selector], 'run'), 'activities'))
        choice = event('choice', 'autonomy.decision', {'selected': {'action': {
            'id': '13390', 'tuning_name': 'fridge_CookGourmetAutonomously'}}})
        self.assertEqual(classify(choice), 'action')
        self.assertEqual(classify(choice, 'future-version'), 'unknown')


if __name__ == '__main__':
    unittest.main()
