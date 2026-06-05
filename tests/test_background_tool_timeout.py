import ast
import os
import unittest


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_TOOLKIT_PY = os.path.abspath(
    os.path.join(_TEST_DIR, "..", "..", "AgenticGis", "core", "toolkit.py")
)


def _parse_toolkit():
    with open(_TOOLKIT_PY) as f:
        return ast.parse(f.read())


class TestBackgroundToolTimeout(unittest.TestCase):
    def test_bg_task_fallback_timeout_constant_defined(self):
        """``run_background_tool`` uses a fallback main-thread path when
        the live layer can't be snapshotted. That fallback must NOT block
        the worker thread forever — the Stop button needs the worker to
        unblock within a bounded time. The constant must be a positive
        number no larger than 10 minutes (so Stop stays responsive).
        """
        tree = _parse_toolkit()
        timeout = None
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "_BG_TASK_FALLBACK_TIMEOUT":
                        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)):
                            timeout = node.value.value
                            break
            if timeout is not None:
                break

        self.assertIsNotNone(
            timeout,
            "core/toolkit.py must define a module-level constant "
            "_BG_TASK_FALLBACK_TIMEOUT used as the executor.run_sync "
            "timeout in the background-tool fallback path.",
        )
        self.assertGreater(timeout, 0,
                           f"Timeout must be positive, got {timeout!r}")
        # 10 minutes is the absolute maximum we'd accept for a single
        # tool fallback; the intended default is 30s.
        self.assertLessEqual(timeout, 600,
                             f"Timeout must be <=600s to keep Stop responsive, got {timeout!r}")

    def test_run_background_tool_does_not_pass_timeout_none(self):
        """Static check: the fallback ``executor.run_sync`` calls in
        ``run_background_tool`` must not use ``timeout=None``.
        """
        tree = _parse_toolkit()

        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "run_background_tool":
                target = node
                break
        self.assertIsNotNone(target,
                             "Could not find run_background_tool in core/toolkit.py")

        for sub in ast.walk(target):
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            if not (isinstance(func, ast.Attribute) and func.attr == "run_sync"):
                continue
            for kw in sub.keywords:
                if (kw.arg == "timeout"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is None):
                    self.fail(
                        f"run_background_tool has executor.run_sync(..., timeout=None) "
                        f"at line {sub.lineno}; this can hang the worker thread forever "
                        f"when Stop is clicked. Use _BG_TASK_FALLBACK_TIMEOUT instead."
                    )


if __name__ == "__main__":
    unittest.main()
