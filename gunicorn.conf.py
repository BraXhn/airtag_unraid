def post_worker_init(worker):
    from app import bridge
    bridge.start()
