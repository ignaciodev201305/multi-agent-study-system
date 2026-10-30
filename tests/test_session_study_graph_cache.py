"""Pruebas de la política LRU de los supervisores de sesión."""

import unittest
from unittest.mock import patch

from core.graphs import SessionStudyGraph


class _StudyPlan:
    """Plan simulado: el builder está parcheado, solo importa la identidad."""


class SessionStudyGraphCacheTests(unittest.TestCase):
    def make_graph(self, max_entries):
        with (
            patch("core.graphs.session.build_model"),
            patch("core.graphs.session.ChainBuilder"),
        ):
            graph = SessionStudyGraph()
        graph.supervisors_cache_max_entries = max_entries
        return graph

    def test_cache_evicts_least_recently_used_supervisor(self):
        graph = self.make_graph(max_entries=2)
        context = _StudyPlan()

        with patch("core.graphs.session.SupervisorBuilder") as builder:
            builder.return_value.build.side_effect = lambda: object()
            supervisor_a = graph._get_or_build_supervisor("a", context)
            graph._get_or_build_supervisor("b", context)
            self.assertIs(
                graph._get_cached_supervisor("a"),
                supervisor_a,
            )
            graph._get_or_build_supervisor("c", context)

        self.assertEqual(len(graph.supervisors), 2)
        self.assertEqual(list(graph.supervisors), ["a", "c"])

    def test_evicted_supervisor_is_rebuilt_from_session_context(self):
        graph = self.make_graph(max_entries=2)
        context = _StudyPlan()

        with patch("core.graphs.session.SupervisorBuilder") as builder:
            builder.return_value.build.side_effect = lambda: object()
            original = graph._get_or_build_supervisor("session", context)
            graph._get_or_build_supervisor("other-a", context)
            graph._get_or_build_supervisor("other-b", context)
            rebuilt = graph._get_or_build_supervisor("session", context)

        self.assertIsNot(original, rebuilt)
        builder.assert_called_with(context)

    def test_deleting_session_removes_its_cached_supervisor(self):
        graph = self.make_graph(max_entries=2)
        with patch("core.graphs.session.SupervisorBuilder") as builder:
            builder.return_value.build.return_value = object()
            graph._get_or_build_supervisor("session", _StudyPlan())

        self.assertTrue(graph.remove_session_cache_entry("session"))
        self.assertFalse(graph.remove_session_cache_entry("session"))
        self.assertEqual(len(graph.supervisors), 0)


if __name__ == "__main__":
    unittest.main()
