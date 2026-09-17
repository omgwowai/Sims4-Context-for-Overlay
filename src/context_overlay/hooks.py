"""Small after-call hooks with explicit ownership and exception isolation."""

import functools
import inspect


def arg(args, kwargs, index, name, default=None):
    return args[index] if len(args) > index else kwargs.get(name, default)


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

    def _install(self, owner, name, wrapped, state):
        original = inspect.getattr_static(owner, name)
        local = name in vars(owner)
        replacement = (staticmethod(wrapped) if isinstance(original, staticmethod) else
                       classmethod(wrapped) if isinstance(original, classmethod) else wrapped)
        setattr(owner, name, replacement)
        self.entries.append((owner, name, original, replacement, state, local))

    @staticmethod
    def _original(owner, name):
        value = inspect.getattr_static(owner, name)
        return value.__func__ if isinstance(value, (staticmethod, classmethod)) else getattr(owner, name)

    def after(self, owner, name, callback):
        original = self._original(owner, name)
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

        self._install(owner, name, wrapped, state)

    def before(self, owner, name, callback):
        original = self._original(owner, name)
        state = {"active": True}

        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            if state["active"]:
                try:
                    callback(args, kwargs)
                except Exception as exc:
                    self._report("{}: {}: {}".format(name, type(exc).__name__, exc))
            return original(*args, **kwargs)

        self._install(owner, name, wrapped, state)

    def around(self, owner, name, before, after):
        """Pair observation state even on EA exceptions; never change EA flow.

        after(context, args, kwargs, result, error) always runs when before
        completed. Callers must not use this wrapper on generators.
        """
        original = self._original(owner, name)
        state = {"active": True}

        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            context, entered, result, error = None, False, None, None
            if state["active"]:
                try:
                    context = before(args, kwargs)
                    entered = True
                except Exception as exc:
                    self._report("{} before: {}".format(name, exc))
            try:
                result = original(*args, **kwargs)
                return result
            except BaseException as exc:
                error = exc
                raise
            finally:
                if entered:
                    try:
                        after(context, args, kwargs, result, error)
                    except Exception as exc:
                        self._report("{} after: {}".format(name, exc))

        self._install(owner, name, wrapped, state)

    def generator(self, owner, name, create, enter, leave, finish):
        """Observe execution of each resume; suspended generators own no frame."""
        original, state, hooks = self._original(owner, name), {"active": True}, self

        class Observer:
            def __init__(self, generator, context):
                self.generator, self.context, self.done = generator, context, False

            def __iter__(self):
                return self

            def _call(self, callback, *args):
                try:
                    callback(self.context, *args)
                except Exception as exc:
                    hooks._report("{} generator: {}".format(name, exc))

            def _step(self, method, *args):
                active = state["active"] and self.context is not None
                result, error, ended = None, None, False
                if active:
                    self._call(enter)
                try:
                    return method(*args)
                except StopIteration as exc:
                    ended, result = True, exc.value
                    raise
                except BaseException as exc:
                    ended, error = True, exc
                    raise
                finally:
                    if active:
                        self._call(leave)
                        if ended and not self.done:
                            self._call(finish, result, error)
                    if ended:
                        self.done = True

            def __next__(self):
                return self._step(next, self.generator)

            def send(self, value):
                return self._step(self.generator.send, value)

            def throw(self, *args):
                return self._step(self.generator.throw, *args)

            def close(self):
                try:
                    return self._step(self.generator.close)
                finally:
                    if state["active"] and not self.done and self.context is not None:
                        self._call(finish, None, GeneratorExit())
                    self.done = True

        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            context = None
            if state["active"]:
                try:
                    context = create(args, kwargs)
                except Exception as exc:
                    hooks._report("{} create: {}".format(name, exc))
            return (yield from Observer(original(*args, **kwargs), context))

        self._install(owner, name, wrapped, state)

    def remove(self):
        for owner, name, original, wrapped, state, local in reversed(self.entries):
            state["active"] = False
            if inspect.getattr_static(owner, name, None) is wrapped:
                if local:
                    setattr(owner, name, original)
                else:
                    delattr(owner, name)
        self.entries[:] = []
