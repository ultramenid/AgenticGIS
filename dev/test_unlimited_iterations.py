"""Regression check for unlimited agent tool-loop iteration mode."""

import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from AgenticGis.backends.base import agent_iteration_steps, unlimited_iterations


def main():
    finite = list(agent_iteration_steps(3))
    assert finite == [0, 1, 2]
    assert not unlimited_iterations(3)

    unlimited = list(itertools.islice(agent_iteration_steps(0), 5))
    assert unlimited == [0, 1, 2, 3, 4]
    assert unlimited_iterations(0)
    assert unlimited_iterations(-1)

    string_unlimited = list(itertools.islice(agent_iteration_steps("0"), 3))
    assert string_unlimited == [0, 1, 2]


if __name__ == "__main__":
    main()
