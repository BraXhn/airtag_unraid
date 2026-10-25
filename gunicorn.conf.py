def post_worker_init(worker):
    from app import bridge, auto_refresh
    bridge.start()
    auto_refresh.start()
