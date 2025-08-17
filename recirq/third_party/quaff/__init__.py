def _register_resolver() -> None:
    from recirq.third_party.quaff.json_resolver import json_resolver
    from recirq.third_party.quaff.json_serialization import _internal_register_resolver

    _internal_register_resolver(json_resolver)


_register_resolver()
