"""Tiny fixture repo for index_cli's end-to-end integration test — real
source, indexed with the real scip-python + scip CLI, not a mock."""


def greet(name: str) -> str:
    return f"hello, {name}"


class Greeter:
    def say_hi(self, name: str) -> str:
        return greet(name)
