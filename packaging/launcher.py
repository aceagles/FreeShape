"""Entry point of the packaged FreeShape.exe."""
import multiprocessing

if __name__ == "__main__":
    # Each tab rebuilds in a worker process, which in a packaged build is this same executable started again.
    # freeze_support() turns such a start into the worker and never returns from it, so it must run first.
    multiprocessing.freeze_support()
    from freeshape.__main__ import main
    main()
