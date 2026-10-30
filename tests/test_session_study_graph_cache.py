"""Pruebas de la política LRU de los supervisores de sesión."""

import unittest
from unittest.mock import patch

from core.graphs import SessionStudyGraph


class _MultiAgentContext:
    def __init__(self):
        self.supervisor = object()
        self.subagents = []


class SessionStudyGraphCacheTests(unittest.TestCase):
    def make_graph(self, max_entries):
        with (
            patch("core.graphs.session.build_model"),
            patch("core.graphs.session.ChainBuilder"),
        ):
            graph = SessionStudyGraph()
        graph.multi_agent_system_cache_max_entries = max_entries
        return graph

    def test_cache_evicts_least_recently_used_supervisor(self):
        graph = self.make_graph(max_entries=2)
        context = _MultiAgentContext()

        with patch("core.graphs.session.MultiAgentSystemBuilder") as builder:
            builder.return_value.build_supervisor.side_effect = lambda _: object()
            supervisor_a = graph._get_or_build_multi_agent_system("a", context)
            graph._get_or_build_multi_agent_system("b", context)
            self.assertIs(
                graph._get_cached_multi_agent_system("a"),
                supervisor_a,
            )
            graph._get_or_build_multi_agent_system("c", context)

        self.assertEqual(len(graph.multi_agent_system), 2)
        self.assertEqual(list(graph.multi_agent_system), ["a", "c"])

    def test_evicted_supervisor_is_rebuilt_from_session_context(self):
        graph = self.make_graph(max_entries=2)
        context = _MultiAgentContext()

        with patch("core.graphs.session.MultiAgentSystemBuilder") as builder:
            builder.return_value.build_supervisor.side_effect = lambda _: object()
            original = graph._get_or_build_multi_agent_system("session", context)
            graph._get_or_build_multi_agent_system("other-a", context)
            graph._get_or_build_multi_agent_system("other-b", context)
            rebuilt = graph._get_or_build_multi_agent_system("session", context)

        self.assertIsNot(original, rebuilt)
        builder.assert_called_with(context.supervisor, context.subagents)
        builder.return_value.build_supervisor.assert_called_with("session")

    def test_deleting_session_removes_its_cached_supervisor(self):
        graph = self.make_graph(max_entries=2)
        with patch("core.graphs.session.MultiAgentSystemBuilder") as builder:
            builder.return_value.build_supervisor.return_value = object()
            graph._get_or_build_multi_agent_system("session", _MultiAgentContext())

        self.assertTrue(graph.remove_session_cache_entry("session"))
        self.assertFalse(graph.remove_session_cache_entry("session"))
        self.assertEqual(len(graph.multi_agent_system), 0)


if __name__ == "__main__":
    unittest.main()
