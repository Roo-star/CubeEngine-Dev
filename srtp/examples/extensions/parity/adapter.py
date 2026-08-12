"""First-party conformance Extension: one pure integer parity predicate."""


class ParityExtension:
    def __init__(self):
        self._initialized = False

    def initialize(self, context):
        if self._initialized:
            raise RuntimeError("already initialized")
        self._initialized = True
        return {"ready": True}

    def invoke(self, capability_id, method, payload):
        if not self._initialized:
            raise RuntimeError("not initialized")
        if method != "is_even":
            raise ValueError("unsupported method")
        arguments = payload["arguments"]
        return arguments[0] % 2 == 0

    def snapshot(self):
        return {"initialized": self._initialized}

    def restore(self, snapshot):
        if not isinstance(snapshot, dict) or set(snapshot) != {"initialized"}:
            raise ValueError("invalid snapshot")
        self._initialized = bool(snapshot["initialized"])
        return {"restored": True}

    def shutdown(self):
        self._initialized = False
        return {"closed": True}


def create_extension():
    return ParityExtension()
