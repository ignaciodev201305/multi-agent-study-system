"""Pruebas de la pizarra: modelo, tools, eventos en vivo y persistencia."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain.messages import AIMessage, HumanMessage
from langchain.tools import ToolRuntime, tool
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command
from pydantic import TypeAdapter, ValidationError

from api.routes import list_chat_messages_endpoint
from core.agents.builder import ADDED_BLOCKS_NOTE, SUPERVISOR_TOOLS, SupervisorBuilder
from core.agents.catalog import ARCHETYPES, archetype_prompt
from core.board import (
    Board,
    BoardBlock,
    BoardError,
    add_block,
    board_inventory,
    clear,
    remove_block,
    update_block,
)
from core.graphs import StudyGraph, compose_session_context, open_checkpointer
from core.schemas import ContextClassification, StudyPlan
from core.streaming import execute_astream_graph
from core.tools import TurnContext, build_tools
from core.tools.board import board_add, board_edit, get_board

PLAN = StudyPlan.model_validate({
    "subject": "derivadas",
    "syllabus": ["Definición"],
    "supervisor_focus": "Principiante.",
    "archetypes": [{"archetype": "explicador", "focus": "Derivadas."}],
})
PENDING = ContextClassification(approved_context=False, questions=["¿Qué nivel tienes?"])
APPROVED = ContextClassification(
    approved_context=True,
    questions=None,
    session_objetive="Parcial",
    session_context="Derivadas",
    previous_level="principiante",
    title_session="Derivadas",
)
MATH_ARGS = {"block": {"kind": "math", "latex": "x^2"}, "title": "Cuadrado"}
OTHER_ARGS = {"block": {"kind": "math", "latex": "y^2"}, "title": "Otra"}
PLOT_ARGS = {"block": {
    "kind": "plot",
    "functions": [{"expression": "sin(x)", "label": "seno"}],
    "x_min": 0,
    "x_max": 3,
}}
BLOCK_ADAPTER = TypeAdapter(BoardBlock)


def _block(**values):
    return BLOCK_ADAPTER.validate_python(values)


MATH = _block(kind="math", latex="x^2")
OTHER_MATH = _block(kind="math", latex="y^2")


class _ToolModel(GenericFakeChatModel):
    # El modelo simulado no puede hacer stream de un mensaje vacío con tool_calls.
    disable_streaming: bool = True

    def bind_tools(self, tools, **kwargs):
        return self


class _Sender:
    def __init__(self):
        self.events = []

    async def send_json(self, payload):
        self.events.append(payload)

    def patches(self):
        return [event for event in self.events if event["type"] == "board_patch"]


class _Runtime:
    def __init__(self, context):
        self.context = context
        self.events = []
        self.stream_writer = self.events.append


@tool
async def emit_events(runtime: ToolRuntime[TurnContext]) -> str:
    """Emite un evento permitido y otros que deben descartarse."""
    runtime.stream_writer({"type": "board_patch", "content": "", "revision": 1})
    runtime.stream_writer({"type": "secreto", "content": "no reenviar"})
    runtime.stream_writer("texto suelto")
    return "ok"


def _call(name, args, call_id):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _script(*messages):
    return _ToolModel(messages=iter(messages))


def _supervisor(supervisor_model, subagent_model, tools=build_tools):
    """Construye el supervisor real con modelos simulados."""
    models = {"supervisor": supervisor_model, "subagent_light": subagent_model}
    with (
        patch("core.agents.builder.build_model", side_effect=lambda role, **_: models[role]),
        patch("core.agents.builder.build_tools", side_effect=tools),
    ):
        return SupervisorBuilder(PLAN).build()


def _filled_board(count):
    """Pizarra con bloques titulados, sin el límite de operaciones de un turno."""
    board = Board()
    with patch("core.board.BOARD_MAX_OPS_PER_TURN", count):
        for index in range(count):
            add_block(board, _block(kind="math", latex=f"x_{index}"), author="a", title=f"Bloque {index}")
    return board


def _chat_graph(supervisor, context):
    """Grafo mínimo que invoca al supervisor igual que el nodo chat."""
    async def chat(state):
        response = await supervisor.ainvoke({"messages": state["messages"]}, context=context)
        return {"messages": [response["messages"][-1]]}

    graph = StateGraph(MessagesState)
    graph.add_node("chat", chat)
    graph.add_edge(START, "chat")
    graph.add_edge("chat", END)
    return graph.compile()


async def _run(supervisor, context):
    sender = _Sender()
    graph = _chat_graph(supervisor, context)
    state, _ = await execute_astream_graph(graph, sender, {"messages": [HumanMessage(content="hola")]}, {})
    return sender, state


class BlockValidationTests(unittest.TestCase):
    def test_accepts_every_kind(self):
        for values in (
            {"kind": "markdown", "text": "**hola**"},
            {"kind": "math", "latex": "x^2"},
            {"kind": "steps", "steps": [{"text": "Deriva"}, {"text": "Evalúa", "latex": "2x"}]},
            {"kind": "table", "headers": ["a", "b"], "rows": [["1", "2"]]},
            {"kind": "code", "language": "python", "code": "print(1)"},
            {"kind": "plot", "functions": [{"expression": "x"}], "x_min": 0, "x_max": 1},
        ):
            self.assertEqual(_block(**values).kind, values["kind"])

    def test_rejects_invalid_blocks(self):
        for values in (
            {"kind": "video", "url": "x"},
            {"kind": "math"},
            {"kind": "math", "latex": ""},
            {"kind": "math", "latex": "x", "id": "b9"},
            {"kind": "steps", "steps": []},
            {"kind": "table", "headers": ["a", "b"], "rows": [["1"]]},
            {"kind": "code", "language": "bash", "code": "ls"},
            {"kind": "plot", "functions": [], "x_min": 0, "x_max": 1},
            {"kind": "plot", "functions": [{"expression": "x"}], "x_min": 1, "x_max": 0},
            {"kind": "plot", "functions": [{"expression": "x"}], "x_min": 0, "x_max": 1, "y_min": 2, "y_max": 1},
            {"kind": "plot", "functions": [{"expression": "x", "points": [[0, 0]]}], "x_min": 0, "x_max": 1},
        ):
            with self.assertRaises(ValidationError, msg=values):
                _block(**values)


class BoardOperationTests(unittest.TestCase):
    def test_ids_and_revision_are_monotonic(self):
        board = Board()
        first = add_block(board, MATH, author="a")
        second = add_block(board, OTHER_MATH, author="a")
        self.assertEqual((first["block"]["id"], first["revision"]), ("b1", 1))
        self.assertEqual((second["block"]["id"], second["revision"]), ("b2", 2))
        remove_block(board, "b2")
        self.assertEqual(clear(board)["revision"], 4)
        self.assertEqual(add_block(board, MATH, author="a")["block"]["id"], "b3")

    def test_patch_contract(self):
        board = Board()
        patch_add = add_block(board, MATH, author="evaluador", title="  Una\nfórmula ")
        self.assertEqual(patch_add, {
            "type": "board_patch",
            "content": "",
            "revision": 1,
            "op": "upsert",
            "block": {"id": "b1", "author": "evaluador", "title": "Una fórmula", "kind": "math", "latex": "x^2"},
        })
        self.assertEqual(remove_block(board, "b1"), {
            "type": "board_patch", "content": "", "revision": 2, "op": "remove", "block_id": "b1",
        })
        self.assertEqual(clear(board), {"type": "board_patch", "content": "", "revision": 3, "op": "clear"})

    def test_rejects_block_with_same_content(self):
        board = Board()
        add_block(board, MATH, author="evaluador", title="Original")
        with self.assertRaisesRegex(BoardError, "ya está en la pizarra como b1"):
            add_block(board, MATH, author="profesor_principal", title="Copia")
        self.assertEqual(len(board.blocks), 1)
        self.assertEqual((board.revision, board.ops_used, board.next_id), (1, 1, 2))

    def test_plot_duplicate_ignores_labels_order_and_y_range(self):
        board = Board()
        add_block(board, _block(
            kind="plot",
            functions=[{"expression": "x", "label": "recta"}, {"expression": "x^2"}],
            x_min=0, x_max=1, y_min=0, y_max=1,
        ), author="a")
        copy = _block(kind="plot", functions=[{"expression": "x^2", "label": "p"}, {"expression": "x"}], x_min=0, x_max=1)
        with self.assertRaisesRegex(BoardError, "como b1"):
            add_block(board, copy, author="a")

    def test_plot_with_other_x_range_is_not_duplicate(self):
        board = Board()
        add_block(board, _block(kind="plot", functions=[{"expression": "x"}], x_min=0, x_max=1), author="a")
        wider = _block(kind="plot", functions=[{"expression": "x"}], x_min=0, x_max=2)
        self.assertEqual(add_block(board, wider, author="a")["block"]["id"], "b2")

    def test_text_duplicate_ignores_case_and_repeated_spaces(self):
        cases = (
            ({"kind": "markdown", "text": "La  Derivada\nes un límite"}, {"kind": "markdown", "text": "la derivada es UN límite"}),
            ({"kind": "steps", "steps": [{"text": "Derivar  Todo", "latex": "x"}]}, {"kind": "steps", "steps": [{"text": "derivar todo", "latex": "x"}]}),
            ({"kind": "table", "headers": ["A  b"], "rows": [["C"]]}, {"kind": "table", "headers": ["a b"], "rows": [["c"]]}),
        )
        for original, copy in cases:
            board = Board()
            add_block(board, _block(**original), author="a")
            with self.assertRaisesRegex(BoardError, "como b1"):
                add_block(board, _block(**copy), author="a")

    def test_latex_and_code_keep_case(self):
        board = Board()
        add_block(board, _block(kind="math", latex="\\Gamma"), author="a")
        add_block(board, _block(kind="math", latex="\\gamma"), author="a")
        add_block(board, _block(kind="code", language="python", code="A = 1"), author="a")
        add_block(board, _block(kind="code", language="python", code="a = 1"), author="a")
        self.assertEqual(len(board.blocks), 4)

    def test_removed_content_can_be_added_again(self):
        board = Board()
        add_block(board, MATH, author="a")
        remove_block(board, "b1")
        self.assertEqual(add_block(board, MATH, author="a")["block"]["id"], "b2")

    def test_update_keeps_identity(self):
        board = Board()
        add_block(board, MATH, author="evaluador", title="Fórmula")
        result = update_block(board, "b1", _block(kind="markdown", text="nuevo"))
        self.assertEqual(result["block"], {
            "id": "b1", "author": "evaluador", "title": "Fórmula", "kind": "markdown", "text": "nuevo",
        })

    def test_unknown_block(self):
        board = Board()
        with self.assertRaisesRegex(BoardError, "get_board"):
            remove_block(board, "b9")
        with self.assertRaisesRegex(BoardError, "get_board"):
            update_block(board, "b9", MATH)
        self.assertEqual(board.revision, 0)
        self.assertEqual(board.ops_used, 0)

    def test_max_blocks(self):
        board = Board()
        with patch("core.board.BOARD_MAX_BLOCKS", 1):
            add_block(board, MATH, author="a")
            with self.assertRaisesRegex(BoardError, "edita o borra"):
                add_block(board, OTHER_MATH, author="a")
        self.assertEqual(len(board.blocks), 1)

    def test_max_block_chars(self):
        with patch("core.board.BOARD_MAX_BLOCK_CHARS", 10):
            with self.assertRaisesRegex(BoardError, "caracteres"):
                add_block(Board(), _block(kind="markdown", text="x" * 50), author="a")

    def test_plot_points_do_not_count_as_content(self):
        board = Board()
        block = _block(kind="plot", functions=[{"expression": "x"}], x_min=0, x_max=1)
        samples = [[[index, index] for index in range(300)]]
        with patch("core.board.BOARD_MAX_BLOCK_CHARS", 200):
            result = add_block(board, block, author="a", samples=samples)
        self.assertEqual(len(result["block"]["functions"][0]["points"]), 300)

    def test_max_plot_functions(self):
        functions = [{"expression": "x"}] * 5
        with self.assertRaisesRegex(BoardError, "4 funciones"):
            add_block(Board(), _block(kind="plot", functions=functions, x_min=0, x_max=1), author="a")

    def test_max_ops_per_turn_counts_every_operation(self):
        board = Board()
        with patch("core.board.BOARD_MAX_OPS_PER_TURN", 3):
            add_block(board, MATH, author="a")
            remove_block(board, "b1")
            clear(board)
            with self.assertRaisesRegex(BoardError, "por turno"):
                clear(board)

    def test_ops_counter_is_not_persisted(self):
        board = Board()
        add_block(board, MATH, author="a")
        dump = board.model_dump()
        self.assertEqual(set(dump), {"blocks", "revision", "next_id"})
        self.assertEqual(Board.model_validate(dump).ops_used, 0)

    def test_inventory_shows_recent_blocks_without_content(self):
        inventory = board_inventory(_filled_board(25).blocks, 20)
        self.assertEqual(len(inventory.splitlines()), 21)
        self.assertIn("5 bloques anteriores", inventory)
        self.assertIn("- b25 (math): Bloque 24", inventory)
        self.assertNotIn("x^2", inventory)


class BoardToolTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self, author="profesor_principal"):
        return _Runtime(TurnContext(session_id="s", author=author))

    async def test_add_emits_patch_with_author(self):
        runtime = self.runtime("evaluador")
        result = await board_add.coroutine(block=MATH, title="Fórmula", runtime=runtime)
        self.assertEqual(result, "Bloque agregado: b1.")
        self.assertEqual(runtime.events[0]["block"]["author"], "evaluador")
        self.assertEqual(runtime.context.board.blocks, [runtime.events[0]["block"]])

    async def test_plot_stores_server_samples(self):
        runtime = self.runtime()
        block = _block(kind="plot", functions=[{"expression": "x^2", "label": "p"}], x_min=0, x_max=2)
        await board_add.coroutine(block=block, runtime=runtime)
        function = runtime.context.board.blocks[0]["functions"][0]
        self.assertEqual((function["expression"], function["label"]), ("x^2", "p"))
        self.assertEqual(len(function["points"]), 200)
        self.assertEqual(function["points"][0], [0, 0])
        self.assertAlmostEqual(function["points"][-1][1], 4)

    async def test_plot_rejects_unsafe_expression_without_adding(self):
        runtime = self.runtime()
        block = _block(kind="plot", functions=[{"expression": "__import__('os')"}], x_min=0, x_max=2)
        result = await board_add.coroutine(block=block, runtime=runtime)
        self.assertTrue(result.startswith("Error:"), result)
        self.assertEqual(runtime.context.board.blocks, [])
        self.assertEqual(runtime.events, [])
        self.assertEqual(runtime.context.board.ops_used, 0)

    async def test_plot_with_foreign_variable_is_error(self):
        runtime = self.runtime()
        block = _block(kind="plot", functions=[{"expression": "sin(t)"}], x_min=0, x_max=2)
        result = await board_add.coroutine(block=block, runtime=runtime)
        self.assertIn("Variables sin valor", result)

    async def test_failed_worker_is_error(self):
        runtime = self.runtime()
        block = _block(kind="plot", functions=[{"expression": "x"}], x_min=0, x_max=2)
        with patch("core.tools.board.run_math", AsyncMock(return_value={})):
            result = await board_add.coroutine(block=block, runtime=runtime)
        self.assertTrue(result.startswith("Error:"), result)

    async def test_limit_is_reported_as_text(self):
        runtime = self.runtime()
        with patch("core.board.BOARD_MAX_BLOCKS", 0):
            result = await board_add.coroutine(block=MATH, runtime=runtime)
        self.assertIn("edita o borra", result)

    async def test_duplicate_is_reported_as_text(self):
        runtime = self.runtime()
        await board_add.coroutine(block=MATH, runtime=runtime)
        result = await board_add.coroutine(block=MATH, title="Copia", runtime=runtime)
        self.assertEqual(result, "Error: Ese contenido ya está en la pizarra como b1: refiérete a ese bloque.")
        self.assertEqual(len(runtime.events), 1)

    async def test_same_plot_is_duplicate(self):
        runtime = self.runtime()
        block = _block(kind="plot", functions=[{"expression": "x"}], x_min=0, x_max=1)
        await board_add.coroutine(block=block, runtime=runtime)
        result = await board_add.coroutine(block=block, runtime=runtime)
        self.assertIn("b1", result)
        self.assertEqual(len(runtime.context.board.blocks), 1)

    async def test_edit_actions(self):
        runtime = self.runtime()
        await board_add.coroutine(block=MATH, runtime=runtime)
        await board_add.coroutine(block=OTHER_MATH, runtime=runtime)

        new_block = _block(kind="markdown", text="nuevo")
        await board_edit.coroutine(action="update", block_id="b1", block=new_block, runtime=runtime)
        self.assertEqual(runtime.context.board.blocks[0]["text"], "nuevo")
        await board_edit.coroutine(action="remove", block_id="b2", runtime=runtime)
        self.assertEqual(len(runtime.context.board.blocks), 1)
        await board_edit.coroutine(action="clear", runtime=runtime)
        self.assertEqual(runtime.context.board.blocks, [])
        self.assertEqual([event["op"] for event in runtime.events[2:]], ["upsert", "remove", "clear"])

    async def test_edit_update_samples_plot(self):
        runtime = self.runtime()
        await board_add.coroutine(block=MATH, runtime=runtime)
        block = _block(kind="plot", functions=[{"expression": "x"}], x_min=0, x_max=1)
        await board_edit.coroutine(action="update", block_id="b1", block=block, runtime=runtime)
        self.assertEqual(len(runtime.context.board.blocks[0]["functions"][0]["points"]), 200)

    async def test_edit_errors_are_text(self):
        runtime = self.runtime()
        for arguments in (
            {"action": "remove"},
            {"action": "update", "block_id": "b1"},
            {"action": "remove", "block_id": "b1"},
        ):
            result = await board_edit.coroutine(runtime=runtime, **arguments)
            self.assertTrue(result.startswith("Error:"), arguments)
        self.assertEqual(runtime.events, [])

    async def test_get_board(self):
        runtime = self.runtime()
        self.assertEqual(await get_board.coroutine(runtime=runtime), "La pizarra está vacía.")
        await board_add.coroutine(block=MATH, title="Fórmula", runtime=runtime)
        self.assertEqual(await get_board.coroutine(runtime=runtime), "- b1 (math): Fórmula")

    def test_supervisor_and_archetypes_get_their_tools(self):
        self.assertLessEqual({"board_add", "board_edit", "get_board"}, set(SUPERVISOR_TOOLS))
        for name in ("explicador", "entrenador_de_ejercicios", "evaluador"):
            self.assertIn("board_add", ARCHETYPES[name].tools)
            self.assertNotIn("board_edit", ARCHETYPES[name].tools)
            self.assertIn("PIZARRA", archetype_prompt(ARCHETYPES[name], "foco"))
        self.assertNotIn("PIZARRA", archetype_prompt(ARCHETYPES["investigador"], "foco"))


class CustomStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_from_subagent_tool_reaches_stream(self):
        supervisor = _supervisor(
            _script(_call("explicador", {"request": "Dibuja."}, "call-1"), AIMessage(content="Listo.")),
            _script(_call("emit_events", {}, "call-2"), AIMessage(content="Hecho.")),
            tools=lambda names: [emit_events] if "math" in names else [],
        )

        sender, _ = await _run(supervisor, TurnContext(session_id="s"))

        self.assertEqual(
            [event for event in sender.events if event["type"] != "thinking"],
            [{"type": "board_patch", "content": "", "revision": 1}],
        )

    async def test_supervisor_and_subagent_write_with_their_author(self):
        supervisor = _supervisor(
            _script(
                _call("board_add", MATH_ARGS, "call-1"),
                _call("explicador", {"request": "Agrega una fórmula."}, "call-2"),
                AIMessage(content="Listo."),
            ),
            _script(_call("board_add", OTHER_ARGS, "call-3"), AIMessage(content="Hecho.")),
        )
        context = TurnContext(session_id="s")

        sender, _ = await _run(supervisor, context)

        patches = sender.patches()
        self.assertEqual([event["revision"] for event in patches], [1, 2])
        self.assertEqual(
            [event["block"]["author"] for event in patches],
            ["profesor_principal", "explicador"],
        )
        self.assertEqual([block["id"] for block in context.board.blocks], ["b1", "b2"])

    async def test_specialist_reports_its_blocks_to_supervisor(self):
        supervisor = _supervisor(
            _script(
                _call("board_add", MATH_ARGS, "call-1"),
                _call("explicador", {"request": "Agrega una fórmula."}, "call-2"),
                AIMessage(content="Listo."),
            ),
            _script(_call("board_add", OTHER_ARGS, "call-3"), AIMessage(content="Explicación.")),
        )

        result = await supervisor.ainvoke(
            {"messages": [HumanMessage(content="hola")]},
            context=TurnContext(session_id="s"),
        )

        report = next(message for message in result["messages"] if message.name == "explicador")
        self.assertTrue(report.content.startswith("Explicación."))
        self.assertIn(ADDED_BLOCKS_NOTE, report.content)
        self.assertIn("- b2 (math): Otra", report.content)
        self.assertNotIn("b1", report.content)

    async def test_specialist_without_blocks_adds_no_note(self):
        supervisor = _supervisor(
            _script(_call("explicador", {"request": "Explica."}, "call-1"), AIMessage(content="Listo.")),
            _script(AIMessage(content="Explicación.")),
        )

        result = await supervisor.ainvoke(
            {"messages": [HumanMessage(content="hola")]},
            context=TurnContext(session_id="s"),
        )

        report = next(message for message in result["messages"] if message.name == "explicador")
        self.assertEqual(report.content, "Explicación.")

    async def test_ops_limit_is_shared_with_subagents(self):
        supervisor = _supervisor(
            _script(
                _call("board_add", MATH_ARGS, "call-1"),
                _call("explicador", {"request": "Agrega una fórmula."}, "call-2"),
                AIMessage(content="Listo."),
            ),
            _script(_call("board_add", OTHER_ARGS, "call-3"), AIMessage(content="Hecho.")),
        )
        context = TurnContext(session_id="s")

        with patch("core.board.BOARD_MAX_OPS_PER_TURN", 1):
            sender, _ = await _run(supervisor, context)

        self.assertEqual(len(sender.patches()), 1)
        self.assertEqual(context.board.ops_used, 1)

    async def test_invalid_block_returns_to_model_as_text(self):
        supervisor = _supervisor(
            _script(
                _call("board_add", {"block": {"kind": "video", "url": "x"}}, "call-1"),
                _call("board_add", MATH_ARGS, "call-2"),
                AIMessage(content="Corregido."),
            ),
            _script(),
        )
        context = TurnContext(session_id="s")

        sender, state = await _run(supervisor, context)

        self.assertEqual(state["messages"][-1].content, "Corregido.")
        self.assertEqual(len(sender.patches()), 1)
        self.assertEqual(len(context.board.blocks), 1)


class BoardStudyGraphTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        with (
            patch("core.graphs.session.build_model"),
            patch("core.graphs.context.build_model"),
            patch("core.graphs.context.ToolsBuilder"),
        ):
            self.study_graph = StudyGraph()
        self.study_graph.context.chain_builder = SimpleNamespace(
            build_chain=lambda *_: SimpleNamespace(ainvoke=AsyncMock(return_value=PLAN))
        )
        self.database = Path(self.directory.name) / "study.db"
        checkpointer = await open_checkpointer(self.database)
        self.graph = await self.study_graph.compile_graph(checkpointer=checkpointer)
        self.config = {"configurable": {"thread_id": "t"}}
        self.sender = _Sender()
        agent = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
            {"structured_response": PENDING},
            {"structured_response": APPROVED},
        ]))
        with patch("core.graphs.context.create_agent", return_value=agent):
            await self.run_turn({"messages": [HumanMessage(content="quiero estudiar")]})
            await self.run_turn(Command(resume="desde cero"))

    async def asyncTearDown(self):
        await self.graph.checkpointer.conn.close()
        self.directory.cleanup()

    def run_turn(self, graph_input):
        return execute_astream_graph(self.graph, self.sender, graph_input, self.config)

    def install_supervisor(self, *messages):
        self.study_graph.session.remove_session_cache_entry("t")
        models = {"supervisor": _script(*messages), "subagent_light": _script()}
        with patch("core.agents.builder.build_model", side_effect=lambda role, **_: models[role]):
            self.study_graph.session._get_or_build_supervisor("t", PLAN)

    def study_turn(self):
        return self.run_turn({"messages": [HumanMessage(content="hola")], "documents": []})

    async def stored_board(self):
        return (await self.graph.aget_state(self.config)).values.get("board")

    async def add_first_block(self):
        self.install_supervisor(_call("board_add", MATH_ARGS, "call-1"), AIMessage(content="Listo."))
        await self.study_turn()

    async def test_turn_persists_board_and_it_survives_reopening(self):
        await self.add_first_block()

        board = await self.stored_board()
        self.assertEqual(board["revision"], 1)
        self.assertEqual(board["next_id"], 2)
        self.assertEqual(board["blocks"][0]["title"], "Cuadrado")
        self.assertEqual(len(self.sender.patches()), 1)

        reopened = await open_checkpointer(self.database)
        try:
            graph = await self.study_graph.compile_graph(checkpointer=reopened)
            self.assertEqual((await graph.aget_state(self.config)).values["board"], board)
        finally:
            await reopened.conn.close()

    async def test_next_turn_sees_board_and_continues_ids(self):
        await self.add_first_block()

        self.install_supervisor(_call("board_add", OTHER_ARGS, "call-2"), AIMessage(content="Otro."))
        await self.study_turn()

        board = await self.stored_board()
        self.assertEqual([block["id"] for block in board["blocks"]], ["b1", "b2"])
        self.assertEqual(board["revision"], 2)

    async def test_failed_turn_keeps_previous_board(self):
        await self.add_first_block()
        before = await self.stored_board()

        # El guion se acaba tras escribir en la pizarra, y el turno falla.
        self.install_supervisor(_call("board_add", OTHER_ARGS, "call-2"))
        with self.assertRaises(Exception):
            await self.study_turn()

        self.assertEqual(len(self.sender.patches()), 2)
        self.assertEqual(await self.stored_board(), before)

    async def test_turn_without_changes_does_not_write_board(self):
        await self.add_first_block()
        before = await self.stored_board()

        self.install_supervisor(AIMessage(content="Sin cambios."))
        await self.study_turn()

        self.assertEqual(await self.stored_board(), before)

    async def test_snapshot_exposes_board(self):
        state = SimpleNamespace(
            study_graph=self.graph,
            session_registry=SimpleNamespace(is_turn_active=lambda chat_id: False),
        )
        request = SimpleNamespace(app=SimpleNamespace(state=state))
        with patch("api.routes.validate_chat_id"):
            empty = await list_chat_messages_endpoint("t", request)
            self.assertEqual(empty["board"], {"blocks": [], "revision": 0, "next_id": 1})

            await self.add_first_block()
            result = await list_chat_messages_endpoint("t", request)
        self.assertEqual(result["board"]["blocks"][0]["id"], "b1")


class SessionContextTests(unittest.TestCase):
    def test_context_lists_board_with_a_limit(self):
        content = compose_session_context({"board": _filled_board(25).model_dump()}).content
        self.assertIn("PIZARRA", content)
        self.assertIn("Bloque 24", content)
        self.assertNotIn("Bloque 4\n", content)
        self.assertNotIn("x^2", content)

    def test_empty_board_is_not_mentioned(self):
        self.assertNotIn("PIZARRA", compose_session_context({}).content)
        self.assertNotIn("PIZARRA", compose_session_context({"board": Board().model_dump()}).content)


if __name__ == "__main__":
    unittest.main()
