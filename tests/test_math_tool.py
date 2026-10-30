"""Pruebas de la tool math: SymPy en subproceso, lista blanca y timeout."""

import unittest
from unittest.mock import patch

from core.tools import symbolic
from core.tools.symbolic import math_tool, run_math


class MathToolTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, **arguments):
        return await math_tool.ainvoke(arguments)

    async def test_derivative(self):
        result = await self.call(operation="diff", expression="sin(x^2)", variable="x")
        self.assertIn("2*x*cos(x**2)", result)
        self.assertIn("LaTeX:", result)

    async def test_definite_integral(self):
        result = await self.call(operation="integrate", expression="2*x", variable="x", lower="0", upper="3")
        self.assertIn("Resultado: 9", result)

    async def test_solve_equation(self):
        result = await self.call(operation="solve", expression="x^2 - 4 = 0", variable="x")
        self.assertIn("-2", result)
        self.assertIn("2", result)

    async def test_simplify_and_evaluate(self):
        self.assertIn("Resultado: 1", await self.call(operation="simplify", expression="sin(x)^2 + cos(x)^2"))
        result = await self.call(operation="evaluate", expression="x*y", substitutions="x=2, y=1/4")
        self.assertIn("0.5", result)

    async def test_limit(self):
        result = await self.call(operation="limit", expression="sin(x)/x", variable="x", point="0")
        self.assertIn("Resultado: 1", result)

    async def test_accepts_long_variable_names(self):
        self.assertIn("cos(theta)", await self.call(operation="diff", expression="sin(theta)", variable="theta"))
        self.assertIn("Resultado: t", await self.call(operation="diff", expression="velocity*t^2/(2*t)", variable="velocity"))

    async def test_e_is_euler_constant(self):
        self.assertIn("Resultado: exp(x)", await self.call(operation="diff", expression="e^x", variable="x"))

    async def test_numeric_bounds_and_point(self):
        self.assertIn("Resultado: 9", await self.call(operation="integrate", expression="2*x", variable="x", lower=0, upper=3))
        self.assertIn("Resultado: 1", await self.call(operation="limit", expression="sin(x)/x", variable="x", point=0))

    async def test_undefined_function_is_error_not_wrong_result(self):
        result = await self.call(operation="diff", expression="f(x)^2", variable="x")
        self.assertTrue(result.startswith("Error:"), result)

    async def test_implicit_multiplication_and_unicode(self):
        self.assertIn("Resultado: x**2 + x", await self.call(operation="expand", expression="x(x+1)"))
        self.assertIn("Resultado: 2*x", await self.call(operation="diff", expression="x²", variable="x"))

    async def test_latex_error_explains_syntax(self):
        result = await self.call(operation="simplify", expression=r"\\frac{1}{2}")
        self.assertIn("no LaTeX", result)

    async def test_rejects_code_injection(self):
        for expression in (
            "__import__('os').system('echo hi')",
            "x.__class__",
            "x.evalf",
            "eval(1)",
            "open(x)",
            "x" * 600,
        ):
            result = await self.call(operation="simplify", expression=expression)
            self.assertTrue(result.startswith("Error:"), expression)

    async def test_timeout_kills_worker(self):
        with patch.object(symbolic, "MATH_TIMEOUT_SECONDS", 0.01):
            response = await run_math({"operation": "simplify", "expression": "x"})
        self.assertIn("tiempo máximo", response["error"])


if __name__ == "__main__":
    unittest.main()
