"""Pruebas de la tool math: SymPy en subproceso, lista blanca y timeout."""

import math
import unittest
from unittest.mock import patch

from pydantic import ValidationError

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

    async def test_limit_with_different_sides_does_not_exist(self):
        result = await self.call(operation="limit", expression="1/x", variable="x", point=0)
        self.assertIn("No existe", result)
        self.assertIn("izquierda -oo", result)
        self.assertIn("derecha oo", result)
        result = await self.call(operation="limit", expression="Abs(x)/x", variable="x", point=0)
        self.assertIn("izquierda -1", result)
        self.assertIn("derecha 1", result)

    async def test_limit_with_equal_sides_keeps_value(self):
        self.assertIn("Resultado: oo", await self.call(operation="limit", expression="1/x^2", variable="x", point=0))

    async def test_limit_ignores_side_outside_real_domain(self):
        result = await self.call(operation="limit", expression="1/sqrt(x)", variable="x", point=0)
        self.assertIn("Resultado: oo", result)

    async def test_limit_at_infinity(self):
        self.assertIn("Resultado: 0", await self.call(operation="limit", expression="1/x", variable="x", point="oo"))

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

    async def test_sample_is_not_exposed_to_the_model(self):
        with self.assertRaises(ValidationError):
            await self.call(operation="sample", expression="x")


class SampleTests(unittest.IsolatedAsyncioTestCase):
    async def sample(self, expression, x_min, x_max, points, variable="x"):
        return await run_math({
            "operation": "sample",
            "expression": expression,
            "variable": variable,
            "x_min": x_min,
            "x_max": x_max,
            "points": points,
        })

    async def test_sin(self):
        samples = (await self.sample("sin(x)", 0, math.pi, 5))["result"]
        self.assertEqual(len(samples), 5)
        for x, y in samples:
            self.assertAlmostEqual(y, math.sin(x))
        self.assertEqual(samples[0][0], 0)
        self.assertAlmostEqual(samples[-1][0], math.pi)

    async def test_constant_expression(self):
        samples = (await self.sample("2", 0, 1, 2))["result"]
        self.assertEqual([y for _, y in samples], [2, 2])

    async def test_tan_is_cut_near_pole(self):
        samples = (await self.sample("tan(x)", 0, math.pi, 101))["result"]
        self.assertAlmostEqual(samples[50][0], math.pi / 2)
        self.assertIsNone(samples[50][1])
        self.assertIsNotNone(samples[49][1])

    async def test_division_by_zero_is_cut(self):
        samples = (await self.sample("1/x", -1, 1, 3))["result"]
        self.assertEqual([y for _, y in samples], [-1, None, 1])

    async def test_outside_real_domain_is_cut(self):
        samples = (await self.sample("sqrt(x)", -1, 1, 3))["result"]
        self.assertEqual([y for _, y in samples], [None, 0, 1])

    async def test_complex_expression_is_error(self):
        self.assertIn("error", await self.sample("x + I", 0, 1, 2))

    async def test_values_over_clip_are_cut(self):
        samples = (await self.sample("exp(x)", 0, 20, 2))["result"]
        self.assertEqual([y for _, y in samples], [1, None])

    async def test_rejects_invalid_requests(self):
        for arguments in (
            ("sin(x)", 0, 1, 301),
            ("sin(x)", 0, 1, 1),
            ("sin(x)", 1, 0, 10),
            ("sin(x)", 0, float("inf"), 10),
            ("x*y", 0, 1, 10),
        ):
            self.assertIn("error", await self.sample(*arguments), arguments)

    async def test_rejects_code_injection(self):
        for expression in ("__import__('os').system('echo hi')", "x.__class__", "open(x)"):
            self.assertIn("error", await self.sample(expression, 0, 1, 10), expression)

    async def test_rejects_invalid_variable(self):
        response = await self.sample("x", 0, 1, 10, variable="__import__")
        self.assertIn("error", response)


if __name__ == "__main__":
    unittest.main()
