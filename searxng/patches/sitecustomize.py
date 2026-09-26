"""Wrapper-side runtime patches for the SearXNG container.

Loaded automatically by Python when this directory is on PYTHONPATH.
"""

from __future__ import annotations

import inspect
import os
import textwrap
import threading
import typing as t

from bootstrap_role import current_process_is_resource_tracker

_ROUND_ROBIN_LOCK = threading.Lock()
_ROUND_ROBIN_CURSOR = 0
_ROUND_ROBIN_PROVIDER_ENV = "SEARXNG_ROUND_ROBIN_PROVIDERS"
_ROUND_ROBIN_DEFAULT_PROVIDERS = (
    "google2",
    "brave2",
    "duckduckgo2",
    "startpage2",
    "bing2",
)


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


def _round_robin_providers() -> tuple[str, ...]:
    raw = os.environ.get(_ROUND_ROBIN_PROVIDER_ENV)
    if raw is None:
        return _ROUND_ROBIN_DEFAULT_PROVIDERS + (("x402exa",) if _engine("x402exa") is not None else ())
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def _is_processor_available(engine_name: str) -> bool:
    from searx.search.processors import PROCESSORS

    processor = PROCESSORS.get(engine_name)
    if processor is None:
        return False
    if processor.suspended_status.is_suspended:
        return False
    return True


def apply_offline_block_suspension_patch() -> None:
    """Give blocking offline-engine failures the stock online suspend path."""
    from searx.exceptions import SearxEngineAccessDeniedException
    from searx.exceptions import SearxEngineCaptchaException
    from searx.exceptions import SearxEngineTooManyRequestsException
    from searx.search.processors.offline import OfflineProcessor

    _require_source(
        "searx.search.processors.offline.OfflineProcessor.search",
        OfflineProcessor.search,
        ("except ValueError as e:", "self.handle_exception(result_container, e)"),
    )
    original = OfflineProcessor.search

    def _patched(self, query, params, result_container, start_time, timeout_limit):
        from searx.engines import _obscura

        if self.engine.name == "x402exa":
            from searx.engines.x402exa import process
            return process(self, query, params, result_container, start_time, timeout_limit)

        reservation_token = params.get(_obscura.RESERVATION_PARAM)
        try:
            # SearXNG must retain provider ownership until it has classified and
            # recorded the complete engine outcome. Releasing after the CDP
            # navigation but before parser/CAPTCHA handling creates a window in
            # which a concurrent search can reserve a provider that is about to
            # be suspended. The shared CDP client deliberately owns no provider
            # scheduling or round-robin state; this lease belongs here at the
            # SearXNG engine-processing boundary.
            with _obscura.provider_lease(
                self.engine.name,
                reservation_token,
                engine_deadline=start_time + timeout_limit,
            ) as pre_navigation_guard:
                params[_obscura.PRE_NAVIGATION_GUARD_PARAM] = pre_navigation_guard
                try:
                    search_results = self.engine.search(query, params)
                    self.extend_container(result_container, start_time, search_results)
                except ValueError as exc:
                    self.logger.exception("engine %s: invalid input: %s", self.engine.name, exc)
                except (
                    SearxEngineCaptchaException,
                    SearxEngineTooManyRequestsException,
                    SearxEngineAccessDeniedException,
                ) as exc:
                    self.handle_exception(result_container, exc, suspend=True)
                    self.logger.exception("engine %s: provider blocked", self.engine.name)
                except Exception as exc:
                    self.handle_exception(result_container, exc)
                    self.logger.exception(
                        "engine %s: exception: %s",
                        self.engine.name,
                        exc.__class__.__name__,
                    )
        finally:
            params.pop(_obscura.PRE_NAVIGATION_GUARD_PARAM, None)
            # This is normally already consumed by provider_lease. Retain the
            # exact-token cleanup for a failed lease entry.
            _obscura.release_provider_reservation(
                self.engine.name, reservation_token
            )

    OfflineProcessor.search = _patched
    print("sitecustomize: patched offline blocking-condition suspension", flush=True)


def _reserve_round_robin_engine(
    engine_names: list[str],
) -> tuple[str | None, str | None]:
    global _ROUND_ROBIN_CURSOR

    if not engine_names:
        return None, None

    from searx.engines import _obscura

    with _ROUND_ROBIN_LOCK:
        for offset in range(len(engine_names)):
            index = (_ROUND_ROBIN_CURSOR + offset) % len(engine_names)
            engine_name = engine_names[index]
            if engine_name == "x402exa":
                from searx.engines import _x402_admission
                token = _x402_admission.reserve(lambda: _is_processor_available(engine_name))
            else:
                token = _obscura.reserve_provider(engine_name)
            if token is not None:
                _ROUND_ROBIN_CURSOR = index + 1
                return engine_name, token
        return None, None


def _round_robin_ref_map(
    engineref_list: list[object],
) -> tuple[dict[str, object], list[str]]:
    provider_order = _round_robin_providers()
    if not provider_order:
        return {}, []

    provider_names = set(provider_order)
    first_ref_by_name = {}
    for engineref in engineref_list:
        name = getattr(engineref, "name", "")
        if name in provider_names and name not in first_ref_by_name:
            first_ref_by_name[name] = engineref

    if not first_ref_by_name:
        return {}, []

    return first_ref_by_name, [
        name for name in provider_order if name in first_ref_by_name
    ]


def _round_robin_selected_refs(
    engineref_list: list[object],
    *,
    exclude: set[str] | None = None,
    wait_for_capacity: bool = False,
) -> tuple[list[object], dict[str, str]]:
    first_ref_by_name, selected_provider_order = _round_robin_ref_map(
        engineref_list
    )
    if not first_ref_by_name:
        return engineref_list, {}

    excluded = exclude or set()
    candidate_provider_order = [
        name for name in selected_provider_order if name not in excluded
    ]
    if not candidate_provider_order:
        return [], {}

    from searx.engines import _obscura

    while True:
        generation = _obscura.provider_capacity_generation()
        available_regular = [
            name
            for name in candidate_provider_order
            if name != "x402exa" and not _is_last_resort_engine(name) and _is_processor_available(name)
        ]
        available_last_resort = [
            name
            for name in candidate_provider_order
            if _is_last_resort_engine(name) and _is_processor_available(name)
        ]
        available_paid = [name for name in candidate_provider_order
                          if name == "x402exa" and _is_processor_available(name)]
        chosen, token = _reserve_round_robin_engine(available_regular)
        # A regular provider that is not suspended but is merely busy or
        # cooling must block last-resort selection. Wait for regular capacity
        # instead of returning an empty result or spilling into Bing.
        if chosen is None and not available_regular:
            chosen, token = _reserve_round_robin_engine(available_last_resort)
        if chosen is None and not available_regular and not available_last_resort:
            chosen, token = _reserve_round_robin_engine(available_paid)
        if chosen is not None and token is not None:
            return [first_ref_by_name[chosen]], {chosen: token}

        wait_names = tuple(available_regular or available_last_resort or available_paid)
        if not wait_for_capacity or not wait_names:
            return [], {}
        if wait_names == ("x402exa",):
            from searx.engines import _x402_admission
            token = _x402_admission.wait(lambda: _is_processor_available("x402exa"))
            if token:
                return [first_ref_by_name["x402exa"]], {"x402exa": token}
        else:
            _obscura.wait_for_provider_capacity_change(generation, wait_names)


def _has_round_robin_provider_pool(engineref_list: list[object]) -> bool:
    first_ref_by_name, _selected_provider_order = _round_robin_ref_map(
        engineref_list
    )
    return bool(first_ref_by_name)


def _record_unavailable_round_robin_providers(
    *,
    engineref_list: list[object],
    exclude: set[str],
    result_container: object,
) -> None:
    from searx.search.processors import PROCESSORS

    _first_ref_by_name, selected_provider_order = _round_robin_ref_map(
        engineref_list
    )
    candidate_provider_order = [
        name for name in selected_provider_order if name not in exclude
    ]
    def tier(name):
        return 2 if name == "x402exa" else int(_is_last_resort_engine(name))
    available = [name for name in candidate_provider_order if _is_processor_available(name)]
    active_tier = min((tier(name) for name in available), default=2)
    for engine_name in candidate_provider_order:
        if tier(engine_name) > active_tier:
            continue
        processor = PROCESSORS.get(engine_name)
        if processor is not None and processor.extend_container_if_suspended(
            result_container
        ):
            continue
        result_container.add_unresponsive_engine(
            engine_name,
            "searx.exceptions.SearxEngineTooManyRequestsException",
        )


def _has_untried_round_robin_provider(
    engineref_list: list[object],
    attempted: set[str],
) -> bool:
    _first_ref_by_name, selected_provider_order = _round_robin_ref_map(
        engineref_list
    )
    return any(name not in attempted for name in selected_provider_order)


def _has_main_results(result_container: object) -> bool:
    return bool(getattr(result_container, "main_results_map", {}))


def apply_round_robin_search_patch() -> None:
    """Optionally schedule one direct-Obscura web provider per SearXNG request."""

    if not _env_enabled("SEARXNG_ROUND_ROBIN", False):
        return

    try:
        import searx.search as search_mod
        from searx.search.processors.abstract import EngineProcessor
    except Exception as exc:  # pragma: no cover
        print(
            f"sitecustomize: failed importing SearXNG search modules: {exc}",
            flush=True,
        )
        _raise_if_strict()
        return

    if getattr(search_mod.Search, "_wrapper_round_robin_patch", False):
        return

    _require_source(
        "searx.search.Search._get_requests",
        search_mod.Search._get_requests,
        (
            "for engineref in self.search_query.engineref_list:",
            "if processor.extend_container_if_suspended(self.result_container):",
            "requests.append((engineref.name, self.search_query.query, request_params))",
        ),
    )
    _require_source(
        "searx.search.Search.search_standard",
        search_mod.Search.search_standard,
        (
            "requests, self.actual_timeout = self._get_requests()",
            "if requests:",
            "self.search_multiple_requests(requests)",
        ),
    )
    _require_source(
        "searx.search.Search.search_multiple_requests",
        search_mod.Search.search_multiple_requests,
        (
            "th._timeout = False",
            "th.join(remaining_time)",
            "th._timeout = True",
            "self.result_container.add_unresponsive_engine(th._engine_name, 'timeout')",
        ),
    )
    _require_source(
        "searx.search.processors.abstract.EngineProcessor.extend_container",
        EngineProcessor.extend_container,
        (
            "if getattr(threading.current_thread(), '_timeout', False):",
            "self.handle_exception(result_container, 'timeout', False)",
            "self._extend_container_basic(result_container, start_time, search_results)",
        ),
    )

    original_get_requests = search_mod.Search._get_requests
    original_search_standard = search_mod.Search.search_standard

    def _patched_get_requests(self):
        from searx.search.processors import PROCESSORS
        original_refs = self.search_query.engineref_list
        if not _has_round_robin_provider_pool(original_refs):
            return original_get_requests(self)
        # Resolve native capabilities once, before admission, and carry the
        # exact params into dispatch. Pool identity is decided before filtering.
        cached = getattr(self, "_wrapper_candidate_params", None)
        if cached is None:
            cached = {}
            _, pool = _round_robin_ref_map(original_refs)
            for ref in original_refs:
                processor = PROCESSORS.get(ref.name)
                if ref.name in pool and processor and ref.name not in cached:
                    cached[ref.name] = processor.get_params(self.search_query, ref.category)
            self._wrapper_candidate_params = cached
        eligible = [ref for ref in original_refs if cached.get(ref.name) is not None]
        excluded = getattr(self, "_wrapper_round_robin_attempted", set())
        if not eligible:
            return [], 0
        selected_refs, reservations = _round_robin_selected_refs(
            eligible, exclude=excluded, wait_for_capacity=True,
        )
        if not selected_refs:
            _record_unavailable_round_robin_providers(
                engineref_list=eligible, exclude=excluded,
                result_container=self.result_container,
            )
            return [], 0
        requests = []
        timeout = 0
        try:
            for ref in selected_refs:
                processor = PROCESSORS[ref.name]
                if processor.extend_container_if_suspended(self.result_container):
                    _release_reservation(ref.name, reservations.get(ref.name))
                    continue
                params = cached[ref.name]
                from searx.engines import _obscura
                parameter = _obscura.RESERVATION_PARAM
                if ref.name == "x402exa":
                    from searx.engines import _x402_admission
                    parameter = _x402_admission.RESERVATION_PARAM
                params[parameter] = reservations[ref.name]
                search_mod.counter_inc('engine', ref.name, 'search', 'count', 'sent')
                requests.append((ref.name, self.search_query.query, params))
                timeout = max(timeout, processor.engine.timeout)
            for bound in (search_mod.settings['outgoing']['max_request_timeout'], self.search_query.timeout_limit):
                if bound is not None:
                    timeout = min(timeout, bound)
            if not requests:
                return self._get_requests()
            return requests, timeout
        except Exception:
            for name, token in reservations.items():
                _release_reservation(name, token)
            raise

    def _patched_search_standard(self):
        if not _has_round_robin_provider_pool(self.search_query.engineref_list):
            return original_search_standard(self)

        self._wrapper_candidate_params = None
        attempted: set[str] = set()
        while True:
            setattr(self, "_wrapper_round_robin_attempted", attempted)
            try:
                requests, self.actual_timeout = self._get_requests()
            finally:
                if hasattr(self, "_wrapper_round_robin_attempted"):
                    delattr(self, "_wrapper_round_robin_attempted")

            if not requests:
                return True

            attempted_this_round = {
                engine_name for engine_name, _query, _params in requests
            }
            attempted.update(attempted_this_round)

            try:
                # Each selected provider is one independent SearXNG engine
                # attempt and receives the configured timeout in full. Provider
                # capacity waiting happens before this clock starts.
                self.start_time = search_mod.default_timer()
                self.search_multiple_requests(requests)
            except Exception:
                from searx.engines import _obscura

                for engine_name, _query, params in requests:
                    _release_reservation(engine_name, params.get(_obscura.RESERVATION_PARAM) or params.get("_wrapper_x402_reservation"))
                raise

            if _has_main_results(self.result_container):
                return True

            # Any attempt without a main result advances to another provider.
            # This covers explicit zero results, parser/navigation failures,
            # blocking responses, and timeouts without needing to classify the
            # result container a second time.
            if not _has_untried_round_robin_provider(
                self.search_query.engineref_list,
                attempted,
            ):
                return True

    search_mod.Search._get_requests = _patched_get_requests
    search_mod.Search.search_standard = _patched_search_standard
    search_mod.Search._wrapper_round_robin_patch = True

    print(
        "sitecustomize: patched SearXNG round-robin provider scheduling and rotation",
        flush=True,
    )


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

    source = textwrap.dedent(inspect.getsource(original_get_ordered_results))
    old_sort = 'results = sorted(self.main_results_map.values(), key=lambda x: x.score, reverse=True)'
    if source.count(old_sort) != 1:
        raise RuntimeError("SearXNG result sorting shape changed")
    source = source.replace(old_sort,
        'results = sorted(self.main_results_map.values(), key=lambda x: (any(not _wrapper_is_last_resort(n) for n in x.engines), x.score), reverse=True)')
    namespace = dict(original_get_ordered_results.__globals__)
    namespace["_wrapper_is_last_resort"] = _is_last_resort_engine
    exec(compile(source, "<wrapper-local-result-sort>", "exec"), namespace)
    _patched_get_ordered_results = namespace[original_get_ordered_results.__name__]

    results_mod.ResultContainer._merge_main_result = _patched_merge_main_result
    results_mod.ResultContainer.close = _patched_close
    results_mod.ResultContainer.get_ordered_results = _patched_get_ordered_results
    results_mod.ResultContainer._wrapper_last_resort_patch = True

    print(
        "sitecustomize: patched SearXNG last-resort engine scoring",
        flush=True,
    )


def _release_reservation(name, token):
    if name == "x402exa":
        from searx.engines import _x402_admission
        _x402_admission.release(token)
    else:
        from searx.engines import _obscura
        _obscura.release_provider_reservation(name, token)


def apply_x402_capability_patch():
    from searx.search.processors.abstract import EngineProcessor
    original = EngineProcessor.get_params
    _require_source("EngineProcessor.get_params", original,
                    ("search_query.pageno > 1", "search_query.time_range"))
    def get_params(self, search_query, engine_category):
        if self.engine.name == "x402exa" and getattr(search_query, "_wrapper_selected_locale", search_query.lang) not in (None, "", "auto", "all"):
            return None
        return original(self, search_query, engine_category)
    EngineProcessor.get_params = get_params


if not current_process_is_resource_tracker():
    try:
        from x402_bootstrap import install, install_locale_adapter
        install()
        apply_offline_block_suspension_patch()
        apply_round_robin_search_patch()
        apply_last_resort_scoring_patch()
        apply_x402_capability_patch()
        install_locale_adapter()
    except Exception:
        # CPython suppresses ordinary sitecustomize exceptions. Do not allow
        # a configured paid engine to disappear while the service stays healthy.
        import sys
        print("sitecustomize: fatal SearXNG bootstrap validation failure", file=sys.stderr, flush=True)
        os._exit(78)
