"""Small after-call hooks with explicit ownership and exception isolation."""

import functools


class Hooks:
    def __init__(self, on_error):
        self.entries = []
        self.on_error = on_error

    def _report(self, message):
        try:
            self.on_error(message)
        except Exception:
            # Even a broken diagnostics sink must not change the EA call.
            pass

    def after(self, owner, name, callback):
        original = getattr(owner, name)
        state = {"active": True}

        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            # Exceptions from EA code deliberately retain their original behavior.
            result = original(*args, **kwargs)
            if state["active"]:
                try:
                    callback(args, kwargs, result)
                except Exception as exc:
                    self._report("{}: {}: {}".format(name, type(exc).__name__, exc))
            return result

        setattr(owner, name, wrapped)
        self.entries.append((owner, name, original, wrapped, state))

    def before(self, owner, name, callback):
        original = getattr(owner, name)
        state = {"active": True}

        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            if state["active"]:
                try:
                    callback(args, kwargs)
                except Exception as exc:
                    self._report("{}: {}: {}".format(name, type(exc).__name__, exc))
            return original(*args, **kwargs)

        setattr(owner, name, wrapped)
        self.entries.append((owner, name, original, wrapped, state))

    def remove(self):
        for owner, name, original, wrapped, state in reversed(self.entries):
            state["active"] = False
            if getattr(owner, name) is wrapped:
                setattr(owner, name, original)
        self.entries[:] = []
