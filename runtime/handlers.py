"""Registro cerrado: funciones puras sintéticas, sin red, PII ni herramientas."""


class TransientFailure(Exception):
    pass


class PermanentFailure(Exception):
    pass


def execute(handler, attempt_number):
    if handler == "record":
        return "synthetic_recorded"
    if handler == "flaky":
        if attempt_number == 1:
            raise TransientFailure
        return "synthetic_recorded"
    if handler == "always_fail":
        raise TransientFailure
    raise PermanentFailure
