"""ContextOverlay Python 3.7 SDK 2.2.0; vendor under your own MOD namespace.

No game/provider imports occur until a method is called. The SDK negotiates
API v2, not an exact MOD version. It never starts a game, thread, or network job.
"""

import importlib


SDK_VERSION = "2.2.0"
__all__ = ["SDK_VERSION", "ContextOverlayError", "Client", "HistoryQuery"]


class ContextOverlayError(RuntimeError):
    def __init__(self, code, message, details=None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(code + ": " + message)

    def to_dict(self):
        return {"code": self.code, "message": self.message, "details": dict(self.details)}


class Client:
    """Reusable across runs; holds no game objects or recording-session state.

    provider is an optional API-shaped test double for offline consumer tests.
    Omit it when calling the installed MOD.
    """
    def __init__(self, provider=None):
        self._provider = provider

    def _api(self):
        provider = self._provider
        if provider is None:
            try:
                provider = importlib.import_module("context_overlay.api")
            except ImportError as exc:
                code = {"context_overlay": "dependency_missing", "context_overlay.api": "incompatible_api"}.get(
                    getattr(exc, "name", None), "provider_error")
                raise ContextOverlayError(code, "Install ContextOverlay with public API v2 (MOD 0.8.0 or later)",
                                          {"reason": str(exc)}) from None
        try:
            info = provider.get_api_info()
            version = info["api_version"].split(".")
            compatible = len(version) == 3 and all(part.isdigit() for part in version) and int(version[0]) == 2
            if not compatible or info["schema_version"] != "2":
                raise ContextOverlayError("incompatible_api", "This SDK requires API 2.x and data schema 2",
                                          {"api_version": info.get("api_version"), "schema_version": info.get("schema_version")})
        except ContextOverlayError:
            raise
        except Exception as exc:
            raise ContextOverlayError("provider_error", "Could not read the provider's API contract",
                                      {"reason": str(exc)}) from None
        return provider, info

    def _call(self, method, *args, **kwargs):
        provider, info = self._api()
        required = {"get_nearby_entities": "context.nearby_entities", "append_event": "events.append",
                    "read_event_changes": "history.changes"}.get(method)
        if method in ("query_event_view", "get_event_view_status", "get_event_view_page", "close_event_view", "explain_event_view"):
            required = "event_views.explain" if method == "explain_event_view" else "event_views.query"
        if required and required not in info.get("capabilities", []):
            raise ContextOverlayError("capability_unavailable", "Provider does not support " + method,
                                      {"required_capability": required,
                                       "api_version": info.get("api_version")})
        try:
            return getattr(provider, method)(*args, **kwargs)
        except Exception as exc:
            error_type = getattr(provider, "APIError", None)
            if isinstance(error_type, type) and isinstance(exc, error_type):
                raise ContextOverlayError(exc.code, exc.message, exc.details) from None
            raise ContextOverlayError("provider_error", "The provider failed outside the public error contract",
                                      {"exception_type": type(exc).__name__, "reason": str(exc)}) from None

    def get_api_info(self):
        """Safe before loading a save; checks dependency and compatibility."""
        return self._api()[1]

    def get_status(self):
        return self._call("get_status")

    def append_event(self, producer, payload, *, expected_session_id, entities=None, idempotency_key=None):
        return self._call("append_event", producer, payload, expected_session_id=expected_session_id,
                          entities=entities, idempotency_key=idempotency_key)

    def read_event_changes(self, checkpoint=None, *, expected_session_id, **options):
        return self._call("read_event_changes", checkpoint, expected_session_id=expected_session_id, **options)

    def changes(self, checkpoint=None, *, expected_session_id, **options):
        """One frozen change batch; save checkpoint after processing all pages."""
        return HistoryQuery(self, self.read_event_changes(checkpoint, expected_session_id=expected_session_id,
                                                         **options), options.get("representation", "both"))

    def get_context(self, kind="sim", identifier="active", **options):
        return self._call("get_context", kind, identifier, **options)

    def get_nearby_entities(self, identifier="active", **options):
        """Read nearby entities when the provider advertises that capability."""
        return self._call("get_nearby_entities", identifier, **options)

    def query_history(self, kind="sim", identifier="active", **options):
        """Low-level first page; caller owns explicit close_history cleanup."""
        return self._call("query_history", kind, identifier, **options)

    def query_event_view(self, view="recap", kind="sim", identifier="active", *, expected_session_id, **options):
        """Submit a view build; do not loop-wait on the simulation thread."""
        return self._call("query_event_view", view, kind, identifier, expected_session_id=expected_session_id, **options)

    def get_event_view_status(self, request_id, *, expected_session_id):
        return self._call("get_event_view_status", request_id, expected_session_id=expected_session_id)

    def get_event_view_page(self, cursor, *, expected_session_id):
        return self._call("get_event_view_page", cursor, expected_session_id=expected_session_id)

    def explain_event_view(self, snapshot_id, item_id, *, expected_session_id, **options):
        return self._call("explain_event_view", snapshot_id, item_id, expected_session_id=expected_session_id, **options)

    def close_event_view(self, request_id, *, expected_session_id):
        return self._call("close_event_view", request_id, expected_session_id=expected_session_id)

    def get_history_page(self, cursor, *, expected_session_id, representation="both"):
        return self._call("get_history_page", cursor, expected_session_id=expected_session_id,
                          representation=representation)

    def close_history(self, cursor, *, expected_session_id):
        return self._call("close_history", cursor, expected_session_id=expected_session_id)

    def history(self, kind="sim", identifier="active", **options):
        """Open a managed query, usable as a context manager or across UI callbacks."""
        return HistoryQuery(self, self.query_history(kind, identifier, **options),
                            options.get("representation", "both"))


class HistoryQuery:
    """A single session-bound query. All operations stay on the game thread.

    page is the complete current response packet; events are page['history']['events'].
    close explicitly when a window closes. No __del__ cleanup on arbitrary threads.
    """
    def __init__(self, client, packet, representation):
        self._client = client
        self._packet = packet
        self._session_id = packet["session_id"]
        self._release_cursor = packet["history"]["cursor"]
        self._next_cursor = packet["history"]["next_cursor"]
        self._checkpoint = packet["history"].get("checkpoint")
        self._representation = representation
        self._closed = False

    @property
    def page(self):
        return self._packet

    @property
    def session_id(self):
        return self._session_id

    @property
    def closed(self):
        return self._closed

    @property
    def checkpoint(self):
        """Available on the last change page; consumer decides when to commit."""
        return self._checkpoint

    @property
    def has_more(self):
        return not self._closed and self._next_cursor is not None

    def next_page(self):
        if self._closed:
            raise ContextOverlayError("query_closed", "Open a new query")
        if self._next_cursor is None:
            return None
        packet = self._client.get_history_page(self._next_cursor, expected_session_id=self._session_id,
                                               representation=self._representation)
        # Update only after success; failed calls leave the same cursor retryable.
        self._packet = packet
        self._next_cursor = packet["history"]["next_cursor"]
        self._checkpoint = packet["history"].get("checkpoint")
        return packet

    def close(self):
        if self._closed:
            return {"released": False, "reason": "already_closed"}
        result = self._client.close_history(self._release_cursor, expected_session_id=self._session_id)
        self._closed = True
        return result

    def __enter__(self):
        if self._closed:
            raise ContextOverlayError("query_closed", "Open a new query")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self.close()
        except ContextOverlayError:
            if exc_type is None:
                raise
            # Do not replace a consumer's original exception with cleanup failure.
            # The query remains bounded by the provider's TTL / run teardown.
        return False
