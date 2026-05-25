def pytest_collection_modifyitems(items):
    original_order = {item: index for index, item in enumerate(items)}
    swarm_mode_order = {
        "enable": 0,
        "exclude": 1,
        "ignore": 2,
        "prefer-local": 3,
        "strict": 4,
    }

    def swarm_mode_sort_key(item):
        callspec = getattr(item, "callspec", None)
        swarm_mode = callspec.params.get("swarm_mode") if callspec is not None else None
        if swarm_mode is None:
            return (0, 0, original_order[item])
        return (1, swarm_mode_order.get(swarm_mode, len(swarm_mode_order)), original_order[item])

    items.sort(key=swarm_mode_sort_key)
