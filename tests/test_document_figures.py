"""Pruebas de las figuras de los PDFs: recorte, descripción con el modelo y marcas en los chunks."""

import base64
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage
from PIL import Image

from documents.pipeline import (
    DocumentPipeline,
    FIGURE_MARKER,
    _extract_figures,
    insert_figure_descriptions,
)


def marker(ref):
    return FIGURE_MARKER.format(ref=ref)


def figure(ref, image="img-a", page=2):
    return {"ref": ref, "page": page, "image": image}


class FakeModel:
    """Modelo falso: responde en orden y registra cada llamada, sin tocar la API."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def ainvoke(self, messages, config=None):
        self.calls.append(messages)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return AIMessage(content=answer)


def pipeline(executor=None):
    with patch("documents.pipeline.build_model", return_value=None):
        return DocumentPipeline(executor, indexer_factory=None)


class InsertFigureDescriptionsTests(unittest.TestCase):
    def test_replaces_described_markers_and_drops_the_rest(self):
        chunks = [{"page_content": f"Grafica:\n{marker('#/pictures/0')} y {marker('#/pictures/1')}", "pages": [1]}]

        result = insert_figure_descriptions(chunks, {"#/pictures/0": "[Figura p.1: parabola]"})

        self.assertEqual(result[0]["page_content"], "Grafica:\n[Figura p.1: parabola] y")
        self.assertEqual(result[0]["pages"], [1])


class DescribeFiguresTests(unittest.IsolatedAsyncioTestCase):
    async def test_describes_each_image_once_and_labels_page(self):
        model = FakeModel("Parabola y = x^2 con vertice en el origen.")

        descriptions = await pipeline().describe_figures(
            [figure("#/pictures/0"), figure("#/pictures/1", page=5)], model, tracker=None
        )

        self.assertEqual(len(model.calls), 1)
        self.assertEqual(descriptions["#/pictures/0"], "[Figura p.2: Parabola y = x^2 con vertice en el origen.]")
        self.assertEqual(descriptions["#/pictures/1"], "[Figura p.5: Parabola y = x^2 con vertice en el origen.]")

    async def test_sends_the_image_to_the_model(self):
        model = FakeModel("Recta")

        await pipeline().describe_figures([figure("#/pictures/0", image="abc")], model, tracker=None)

        content = model.calls[0][0].content
        self.assertEqual(content[1]["image_url"]["url"], "data:image/png;base64,abc")

    async def test_skips_decorative_figures_and_failed_calls(self):
        model = FakeModel("NADA", RuntimeError("rate limit"), "Circulo unitario")
        figures = [figure("#/pictures/0", "a"), figure("#/pictures/1", "b"), figure("#/pictures/2", "c", page=None)]

        descriptions = await pipeline().describe_figures(figures, model, tracker=None)

        self.assertEqual(descriptions, {"#/pictures/2": "[Figura: Circulo unitario]"})


class ProcessWithFiguresTests(unittest.IsolatedAsyncioTestCase):
    async def test_study_map_receives_figure_descriptions(self):
        chunks = [{"page_content": f"Ejercicio 1\n{marker('#/pictures/0')}", "pages": [3]}]
        executor = SimpleNamespace(extract=AsyncMock(return_value=(chunks, [figure("#/pictures/0", page=3)])))
        document_pipeline = pipeline(executor)
        document_pipeline.model = FakeModel("Grafico de seno en [0, 2pi]")
        document_pipeline._summarize = AsyncMock(return_value=SimpleNamespace(model_dump=lambda: {"title": "t"}))

        summary, indexed = await document_pipeline.process(
            {"chat_id": "chat", "document_type": "study_map"}, "doc.pdf", tracker=None
        )

        sent_chunks = document_pipeline._summarize.await_args.args[0]
        self.assertEqual(sent_chunks[0]["page_content"], "Ejercicio 1\n[Figura p.3: Grafico de seno en [0, 2pi]]")
        self.assertEqual((summary, indexed), ({"title": "t"}, 0))


class ExtractFiguresTests(unittest.TestCase):
    def picture(self, ref, size, page=1):
        image = Image.new("RGB", size, "white")
        return SimpleNamespace(
            self_ref=ref,
            prov=[SimpleNamespace(page_no=page)],
            get_image=lambda doc: image,
        )

    def test_skips_small_images_resizes_large_ones_and_caps_count(self):
        document = SimpleNamespace(pictures=[
            self.picture("#/pictures/0", (40, 40)),
            self.picture("#/pictures/1", (1600, 800), page=4),
            self.picture("#/pictures/2", (300, 300)),
            self.picture("#/pictures/3", (300, 300)),
        ])

        figures = _extract_figures(document, max_figures=2)

        self.assertEqual([item["ref"] for item in figures], ["#/pictures/1", "#/pictures/2"])
        self.assertEqual(figures[0]["page"], 4)
        image = Image.open(io.BytesIO(base64.b64decode(figures[0]["image"])))
        self.assertEqual(image.size, (768, 384))


if __name__ == "__main__":
    unittest.main()
