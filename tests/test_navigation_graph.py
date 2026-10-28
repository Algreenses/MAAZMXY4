"""Pure navigation contract tests; no game, GUI, OCR models or native SDK."""
from copy import deepcopy
import math
import unittest

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from navigation import NavigationError, select_state, shortest_path, validate_navigation


def graph(*ids, targets=None):
    return dict(version=1, targets=list(targets or [ids[-1]]),
                nodes=[dict(id=name, label=name, recognition=["see_" + name]) for name in ids],
                edges=[], max_steps=30, max_seconds=120, settle_ms=300, locate_attempts=3)


def edge(source, target, cost=1, name=None):
    name = name or source + "_to_" + target
    return dict(id=name, source=source, target=target, label=name, entry="do_" + name,
                cost=cost, timeout_ms=1000, max_attempts=1)


def pipeline(nav):
    names = {name for node in nav["nodes"] for key in ("recognition", "any", "exclude")
             for name in node.get(key, [])}
    result = {name: dict(recognition=dict(type="OCR", param=dict(expected=[name])),
                         action=dict(type="DoNothing", param={})) for name in names}
    for item in nav["edges"]:
        result[item["entry"]] = dict(action=dict(type="Click", param={}))
    return result


class NavigationValidationTests(unittest.TestCase):
    def setUp(self):
        self.nav = graph("home", "map", "stage")
        self.nav["edges"] = [edge("home", "map"), edge("map", "stage")]

    def test_validation_returns_none_and_does_not_modify_inputs(self):
        nodes = pipeline(self.nav)
        before = deepcopy((self.nav, nodes))
        self.assertIsNone(validate_navigation(self.nav, nodes))
        self.assertEqual((self.nav, nodes), before)

    def test_optional_lists_and_empty_edges_are_valid(self):
        nav = graph("home")
        nav["nodes"][0].update(any=[], exclude=[], obscures=[])
        validate_navigation(nav, pipeline(nav))

    def test_rejects_invalid_top_level_structure(self):
        for value in (None, [], {}, dict(self.nav, version=True), dict(self.nav, version=2),
                      dict(self.nav, targets=[]), dict(self.nav, targets="stage"),
                      dict(self.nav, targets=["missing"]), dict(self.nav, targets=["stage", "stage"]),
                      dict(self.nav, nodes=[]), dict(self.nav, edges={}), dict(self.nav, priority=1)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_navigation(value, {})

    def test_budgets_reject_bool_negative_zero_and_out_of_bounds(self):
        for key, values in {
            "max_steps": (True, 0, -1, 1001, math.inf),
            "max_seconds": (True, 0, 3601, "120"),
            "settle_ms": (False, -1, 10001, .5),
            "locate_attempts": (True, 0, 21),
        }.items():
            for value in values:
                nav = deepcopy(self.nav)
                nav[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    validate_navigation(nav, pipeline(nav))

    def test_rejects_duplicate_nodes_or_edges(self):
        for field in ("nodes", "edges"):
            nav = deepcopy(self.nav)
            nav[field].append(deepcopy(nav[field][0]))
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_navigation(nav, pipeline(nav))

    def test_rejects_empty_all_conditions_and_malformed_condition_lists(self):
        for update in ({"recognition": []}, {"recognition": "see_home"}, {"recognition": [None]},
                       {"recognition": ["see_home", "see_home"]}, {"any": "see_home"},
                       {"exclude": [False]}, {"obscures": ["missing"]}, {"priority": 99}):
            nav = deepcopy(self.nav)
            nav["nodes"][0].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_navigation(nav, {})

    def test_rejects_contradictory_conditions(self):
        for update in ({"exclude": ["see_home"]}, {"any": ["other"], "exclude": ["other"]}):
            nav = deepcopy(self.nav)
            nav["nodes"][0].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_navigation(nav, pipeline(nav))

    def test_rejects_self_and_transitive_obscures_cycles(self):
        for relations in ((["home"], [], []), (["map"], ["stage"], ["home"])):
            nav = deepcopy(self.nav)
            for node, obscures in zip(nav["nodes"], relations):
                node["obscures"] = obscures
            with self.subTest(relations=relations), self.assertRaises(ValueError):
                validate_navigation(nav, pipeline(nav))

    def test_every_recognition_reference_must_exist_and_be_read_only(self):
        nav = deepcopy(self.nav)
        nav["nodes"][0].update(any=["alternate"], exclude=["dialog"])
        for name in ("see_home", "alternate", "dialog"):
            for action in (None, {"type": "Click"}, {"type": "Custom"}, {"type": "StopTask"}):
                nodes = pipeline(nav)
                if action is None:
                    nodes.pop(name)
                else:
                    nodes[name]["action"] = action
                with self.subTest(name=name, action=action), self.assertRaises(ValueError):
                    validate_navigation(nav, nodes)

    def test_pipeline_none_is_not_a_valid_complete_snapshot(self):
        with self.assertRaises(ValueError):
            validate_navigation(self.nav, None)

    def test_rejects_missing_edge_entry_or_endpoints(self):
        for field in ("entry", "source", "target"):
            nav = deepcopy(self.nav)
            nodes = pipeline(nav)
            nav["edges"][0][field] = "missing"
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_navigation(nav, nodes)

    def test_rejects_invalid_edge_cost_and_attempt_budget(self):
        for field, values in {
            "cost": (0, -1, True, math.inf, math.nan, "1", 1000001),
            "timeout_ms": (0, -1, True, 120001),
            "max_attempts": (0, 3, True, 1.5),
        }.items():
            for value in values:
                nav = deepcopy(self.nav)
                nav["edges"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_navigation(nav, pipeline(nav))

    def test_navigation_cycles_are_valid_but_obscures_cycles_are_not(self):
        self.nav["edges"].extend([edge("map", "home"), edge("map", "map")])
        validate_navigation(self.nav, pipeline(self.nav))


class ScreenSelectionTests(unittest.TestCase):
    def setUp(self):
        self.nav = graph("home", "panel", "dialog")

    def assert_reason(self, reason, call, *args):
        with self.assertRaises(NavigationError) as result:
            call(*args)
        self.assertEqual(result.exception.reason, reason)
        return result.exception

    def test_unknown_is_explicit_and_unrelated_hits_do_not_match(self):
        self.assert_reason("unknown", select_state, self.nav, set())
        self.assert_reason("unknown", select_state, self.nav, {"unrelated"})

    def test_all_any_and_exclude_have_distinct_semantics(self):
        self.nav["nodes"][0].update(recognition=["title", "anchor"],
                                     any=["icon", "label"], exclude=["dimmed"])
        for hitnames in ({"title", "icon"}, {"title", "anchor"},
                         {"title", "anchor", "icon", "dimmed"}):
            self.assert_reason("unknown", select_state, self.nav, hitnames)
        self.assertEqual(select_state(self.nav, {"title", "anchor", "icon"}), "home")
        self.assertEqual(select_state(self.nav, {"title", "anchor", "label"}), "home")

    def test_ambiguity_never_uses_list_order_as_priority(self):
        error = self.assert_reason("ambiguous", select_state, self.nav, {"see_home", "see_panel"})
        self.assertEqual(set(error.candidates), {"home", "panel"})
        self.nav["nodes"].reverse()
        self.assert_reason("ambiguous", select_state, self.nav, {"see_home", "see_panel"})

    def test_dialog_obscures_underlying_page(self):
        self.nav["nodes"][2]["obscures"] = ["home"]
        self.assertEqual(select_state(self.nav, {"see_home", "see_dialog"}), "dialog")

    def test_obscures_closure_includes_unmatched_intermediate_panel(self):
        self.nav["nodes"][2]["obscures"] = ["panel"]
        self.nav["nodes"][1]["obscures"] = ["home"]
        self.assertEqual(select_state(self.nav, {"see_home", "see_dialog"}), "dialog")
        self.assertEqual(select_state(self.nav, {"see_home", "see_panel", "see_dialog"}), "dialog")

    def test_unmatched_dialog_does_not_hide_a_matching_page(self):
        self.nav["nodes"][2]["obscures"] = ["home"]
        self.assertEqual(select_state(self.nav, {"see_home"}), "home")

    def test_two_overlays_on_same_page_remain_ambiguous(self):
        self.nav["nodes"][1]["obscures"] = ["home"]
        self.nav["nodes"][2]["obscures"] = ["home"]
        error = self.assert_reason("ambiguous", select_state, self.nav,
                                   {"see_home", "see_panel", "see_dialog"})
        self.assertEqual(set(error.candidates), {"panel", "dialog"})

    def test_selection_does_not_modify_graph_or_hits(self):
        before = deepcopy(self.nav)
        hits = frozenset({"see_home"})
        self.assertEqual(select_state(self.nav, hits), "home")
        self.assertEqual(self.nav, before)
        self.assertEqual(hits, {"see_home"})

    def test_malformed_hit_container_is_rejected(self):
        for value in (None, "see_home", ["see_home"], {False}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                select_state(self.nav, value)


class RoutePlanningTests(unittest.TestCase):
    def setUp(self):
        self.nav = graph("home", "menu", "map", "stage")
        self.nav["edges"] = [edge("home", "menu"), edge("menu", "stage", 4),
                             edge("home", "map", 2), edge("map", "stage")]

    def assert_route(self, start, expected, blocked=()):
        result = shortest_path(self.nav, start, blocked)
        self.assertEqual([item["id"] for item in result], expected)
        return result

    def test_cheapest_route_uses_cost_not_edge_count_or_input_order(self):
        self.nav["edges"].insert(0, edge("home", "stage", 10))
        self.assert_route("home", ["home_to_map", "map_to_stage"])

    def test_already_at_any_target_is_empty_route(self):
        self.nav["targets"].append("menu")
        self.assert_route("stage", [])
        self.assert_route("menu", [])
        self.assert_route("home", ["home_to_menu"])

    def test_blocked_edge_uses_alternative_without_mutating_graph(self):
        before = deepcopy(self.nav)
        self.assert_route("home", ["home_to_menu", "menu_to_stage"], ["home_to_map"])
        self.assertEqual(self.nav, before)

    def test_result_has_independent_edge_dictionaries(self):
        result = self.assert_route("home", ["home_to_map", "map_to_stage"])
        result[0]["entry"] = "mutated"
        self.assertNotEqual(self.nav["edges"][2]["entry"], "mutated")

    def test_positive_cost_cycles_and_self_loops_do_not_loop_search(self):
        self.nav["edges"].extend([edge("menu", "home", .01), edge("menu", "menu", .01),
                                  edge("map", "home", .01)])
        self.assert_route("home", ["home_to_map", "map_to_stage"])

    def test_equal_cost_routes_are_deterministic(self):
        self.nav["edges"][1]["cost"] = 2
        for _ in range(3):
            self.assert_route("home", ["home_to_menu", "menu_to_stage"])

    def test_step_budget_uses_feasible_more_expensive_route(self):
        self.nav["edges"].append(edge("home", "stage", 10))
        self.nav["max_steps"] = 1
        self.assert_route("home", ["home_to_stage"])

    def test_cheaper_long_prefix_does_not_suppress_short_feasible_prefix(self):
        self.nav = graph("start", "detour", "junction", "target")
        self.nav["max_steps"] = 2
        self.nav["edges"] = [edge("start", "detour", 1), edge("detour", "junction", 1),
                             edge("start", "junction", 5), edge("junction", "target", 1)]
        self.assert_route("start", ["start_to_junction", "junction_to_target"])

    def test_reachable_but_over_step_budget_has_distinct_failure(self):
        self.nav["max_steps"] = 1
        with self.assertRaises(NavigationError) as result:
            shortest_path(self.nav, "home")
        self.assertEqual(result.exception.reason, "budget")

    def test_unreachable_after_blocking_edges_is_explicit(self):
        with self.assertRaises(NavigationError) as result:
            shortest_path(self.nav, "home", ["menu_to_stage", "map_to_stage"])
        self.assertEqual(result.exception.reason, "unreachable")

    def test_unknown_start_has_distinct_failure(self):
        with self.assertRaises(NavigationError) as result:
            shortest_path(self.nav, "missing")
        self.assertEqual(result.exception.reason, "invalid_start")

    def test_invalid_blocked_edges_are_not_silently_ignored(self):
        for blocked in (["missing"], "home_to_map", {False}):
            with self.subTest(blocked=blocked), self.assertRaises(ValueError):
                shortest_path(self.nav, "home", blocked)


if __name__ == "__main__":
    unittest.main()
