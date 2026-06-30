"""Wrapper-side runtime patches for the SearXNG container.

Loaded automatically by Python when this directory is on PYTHONPATH.
"""

from __future__ import annotations

import inspect
import os
import typing as t


def _env_enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _strict_mode() -> bool:
    return _env_enabled("WRAPPER_PATCH_STRICT", True)


def _warn_or_raise(message: str) -> None:
    print(f"sitecustomize: WARNING: {message}", flush=True)
    if _strict_mode():
        raise RuntimeError(message)


def _raise_if_strict() -> None:
    if _strict_mode():
        raise


def _require_source(owner_name: str, obj: t.Any, fragments: tuple[str, ...]) -> None:
    try:
        source = inspect.getsource(obj)
    except Exception as exc:  # pragma: no cover
        _warn_or_raise(f"could not inspect {owner_name}: {exc}")
        return

    missing = [fragment for fragment in fragments if fragment not in source]
    if missing:
        _warn_or_raise(
            f"{owner_name} no longer matches expected upstream scoring shape; "
            f"missing fragments: {missing!r}"
        )


def _float_engine_attr(engine: object, name: str, default: float) -> float:
    raw = getattr(engine, name, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        _warn_or_raise(
            f"engine {getattr(engine, 'name', '<unknown>')} has invalid "
            f"{name}={raw!r}; expected a number"
        )
        return default


def _engine(name: str) -> object | None:
    import searx.engines

    return searx.engines.engines.get(name)


def _is_last_resort_engine(name: str) -> bool:
    engine = _engine(name)
    if engine is None:
        return False
    if getattr(engine, "last_resort", False) is True:
        return True
    return getattr(engine, "score_mode", "") == "last_resort"


def _score_positions(
    *,
    engine_names: set[str],
    positions: list[int],
    priority: str,
) -> float:
    if priority == "low" or not positions:
        return 0.0

    weight = 1.0
    for engine_name in engine_names:
        engine = _engine(engine_name)
        if engine is not None and hasattr(engine, "weight"):
            weight *= _float_engine_attr(engine, "weight", 1.0)

    weight *= len(positions)

    if priority == "high":
        return sum(weight for _position in positions)

    return sum(weight / position for position in positions if position > 0)


def _score_last_resort_only(
    *,
    engine_positions: dict[str, list[int]],
    priority: str,
) -> float:
    if priority == "low":
        return 0.0

    score = 0.0
    for engine_name, positions in engine_positions.items():
        if not positions:
            continue
        engine = _engine(engine_name)
        if engine is None:
            continue
        weight = _float_engine_attr(
            engine,
            "last_resort_fallback_weight",
            _float_engine_attr(engine, "weight", 0.05),
        )
        weight *= len(positions)
        if priority == "high":
            score += sum(weight for _position in positions)
        else:
            score += sum(weight / position for position in positions if position > 0)
    return score


def _score_with_last_resort(
    result: object,
    priority: str,
    engine_positions: dict[str, list[int]],
    original_calculate_score: t.Callable[[object, str], float],
) -> float:
    last_resort_engines = {
        name for name in engine_positions if _is_last_resort_engine(name)
    }
    if not last_resort_engines:
        return original_calculate_score(result, priority)

    regular_engines = set(engine_positions) - last_resort_engines
    regular_positions = [
        position
        for engine_name in regular_engines
        for position in engine_positions.get(engine_name, [])
    ]

    if not regular_positions:
        return _score_last_resort_only(
            engine_positions={
                name: engine_positions.get(name, [])
                for name in last_resort_engines
            },
            priority=priority,
        )

    score = _score_positions(
        engine_names=regular_engines,
        positions=regular_positions,
        priority=priority,
    )
    if score <= 0:
        return score

    confirmation_bonus = 0.0
    for engine_name in last_resort_engines:
        if not engine_positions.get(engine_name):
            continue
        engine = _engine(engine_name)
        if engine is None:
            continue
        confirmation_bonus += _float_engine_attr(
            engine,
            "last_resort_confirmation_bonus",
            0.15,
        )

    return score * (1.0 + confirmation_bonus)


def apply_last_resort_scoring_patch() -> None:
    """Make configured fallback engines confirm results without poisoning them."""

    try:
        import searx.results as results_mod
    except Exception as exc:  # pragma: no cover
        print(
            f"sitecustomize: failed importing SearXNG result modules: {exc}",
            flush=True,
        )
        _raise_if_strict()
        return

    if getattr(results_mod.ResultContainer, "_wrapper_last_resort_patch", False):
        return

    _require_source(
        "searx.results.calculate_score",
        results_mod.calculate_score,
        (
            "weight *= float(searx.engines.engines[result_engine].weight)",
            "weight *= len(result['positions'])",
            "score += weight / position",
        ),
    )
    _require_source(
        "searx.results.ResultContainer._merge_main_result",
        results_mod.ResultContainer._merge_main_result,
        (
            "result.positions = [position]",
            "merge_two_main_results(merged, result)",
            "merged.positions.append(position)",
        ),
    )
    _require_source(
        "searx.results.ResultContainer.close",
        results_mod.ResultContainer.close,
        (
            "result.score = calculate_score(result, result.priority)",
            "counter_add(result.score, 'engine', eng_name, 'score')",
        ),
    )
    _require_source(
        "searx.results.ResultContainer.get_ordered_results",
        results_mod.ResultContainer.get_ordered_results,
        (
            'results = sorted(self.main_results_map.values(), key=lambda x: x.score, reverse=True)',
            "categoryPositions",
            "gresults.insert(index, res)",
        ),
    )

    original_calculate_score = results_mod.calculate_score
    original_get_ordered_results = results_mod.ResultContainer.get_ordered_results

    def _engine_positions(container: object) -> dict[int, dict[str, list[int]]]:
        positions = getattr(container, "_wrapper_engine_positions", None)
        if positions is None:
            positions = {}
            setattr(container, "_wrapper_engine_positions", positions)
        return positions

    def _patched_merge_main_result(self, result, position):
        result_hash = hash(result)
        engine_name = result.engine or ""

        with self._lock:
            positions_by_hash = _engine_positions(self)
            positions_by_engine = positions_by_hash.setdefault(result_hash, {})
            positions_by_engine.setdefault(engine_name, []).append(position)

            merged = self.main_results_map.get(result_hash)
            if not merged:
                result.positions = [position]
                self.main_results_map[result_hash] = result
                return

            results_mod.merge_two_main_results(merged, result)
            merged.positions.append(position)

    def _patched_close(self):
        self._closed = True

        positions_by_hash = _engine_positions(self)
        for result_hash, result in self.main_results_map.items():
            result_engine_positions = positions_by_hash.get(result_hash, {})
            if not result_engine_positions:
                result_engine_positions = {
                    engine_name: list(result.positions)
                    for engine_name in result.engines
                }
            result.score = _score_with_last_resort(
                result,
                result.priority,
                result_engine_positions,
                original_calculate_score,
            )
            for eng_name in result.engines:
                results_mod.counter_add(result.score, "engine", eng_name, "score")

    def _has_regular_engine(result: object) -> bool:
        engines = getattr(result, "engines", set())
        return any(not _is_last_resort_engine(engine_name) for engine_name in engines)

    def _patched_get_ordered_results(self):
        if not self._closed:
            self.close()

        if self._main_results_sorted:
            return self._main_results_sorted

        original_sorted = sorted

        def _wrapper_sorted(iterable, *, key=None, reverse=False):
            return original_sorted(
                iterable,
                key=lambda result: (1 if _has_regular_engine(result) else 0, result.score),
                reverse=True,
            )

        had_sorted_global = "sorted" in original_get_ordered_results.__globals__
        saved_sorted = original_get_ordered_results.__globals__.get("sorted")
        original_get_ordered_results.__globals__["sorted"] = _wrapper_sorted
        try:
            return original_get_ordered_results(self)
        finally:
            if had_sorted_global:
                original_get_ordered_results.__globals__["sorted"] = saved_sorted
            else:
                del original_get_ordered_results.__globals__["sorted"]

    results_mod.ResultContainer._merge_main_result = _patched_merge_main_result
    results_mod.ResultContainer.close = _patched_close
    results_mod.ResultContainer.get_ordered_results = _patched_get_ordered_results
    results_mod.ResultContainer._wrapper_last_resort_patch = True

    print(
        "sitecustomize: patched SearXNG last-resort engine scoring",
        flush=True,
    )


apply_last_resort_scoring_patch()
