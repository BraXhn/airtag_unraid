def post_worker_init(worker):
    from app import workspaces
    workspaces.start()


def worker_exit(server, worker):
    from app import workspaces
    workspaces.close()
