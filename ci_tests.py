"""Run every discovered unittest in bounded, isolated CI processes (Python 3.9+)."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import contextlib
import io
import multiprocessing
from pathlib import Path
import sys
import time
import unittest


def test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_ids(test)
        else:
            yield test.id()


def partition(names, workers):
    # Keep module/class fixtures in one process. Preserve repeated imported tests,
    # too: the serial discovery suite currently includes some duplicate IDs.
    modules = {}
    for name in names:
        modules.setdefault(name.rsplit('.', 2)[0], []).append(name)
    groups = [[] for _ in range(min(workers, len(modules)))]
    for module in sorted(modules.values(), key=lambda values: (-len(values), values[0])):
        min(groups, key=len).extend(module)
    return groups


def run_group(names, root):
    sys.path.insert(0, root)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(names)
    if loader.errors or suite.countTestCases() != len(names):
        raise RuntimeError('Test loading changed the discovered suite: ' + '\n'.join(loader.errors))
    output = io.StringIO()
    started = time.monotonic()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    return result.wasSuccessful(), output.getvalue(), result.testsRun, len(result.skipped), time.monotonic() - started


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, choices=(1, 2), default=2)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    root = str(args.root.resolve())
    loader = unittest.TestLoader()
    suite = loader.discover(root)
    names = list(test_ids(suite))
    if loader.errors or not names:
        print('\n'.join(loader.errors) or 'No tests discovered; refusing an empty CI run.', file=sys.stderr)
        return 1
    groups = partition(names, args.workers)
    print('Discovered %d tests; running %d isolated workers (whole modules).' % (len(names), len(groups)), flush=True)
    success = True
    total = skipped = 0
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=len(groups), mp_context=multiprocessing.get_context('spawn')) as pool:
        futures = {pool.submit(run_group, group, root): number for number, group in enumerate(groups, 1)}
        for future in as_completed(futures):
            number = futures[future]
            try:
                passed, log, count, skips, elapsed = future.result()
            except Exception as error:
                success = False
                print('Worker %d failed: %r' % (number, error), file=sys.stderr, flush=True)
                continue
            print('\n=== Worker %d: %d tests, %d skipped, %.2fs ===\n%s' % (number, count, skips, elapsed, log), flush=True)
            total += count
            skipped += skips
            success = success and passed
    print('CI result: %d tests run, %d skipped, %.2fs, %s' % (total, skipped, time.monotonic() - started,
                                                            'PASS' if success else 'FAIL'), flush=True)
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
