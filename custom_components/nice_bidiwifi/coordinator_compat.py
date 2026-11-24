"""Static compatibility properties for existing HA adapters and internal callers.

State and methods belong to the named components. There is no dynamic attribute
forwarding, and new runtime code addresses each owner explicitly.
"""


def _component_property(path: str):
    names = path.split(".")

    def parent(instance):
        for name in names[:-1]:
            instance = getattr(instance, name)
        return instance

    def get(instance):
        return getattr(parent(instance), names[-1])

    def set_value(instance, value):
        setattr(parent(instance), names[-1], value)

    return property(get, set_value)


class CoordinatorCompatibility:
    """Keep the established facade while component state ownership is explicit."""

    _abnormal_scan_monotonic = _component_property(
        "event_controller._abnormal_scan_monotonic"
    )
    _accept_events = _component_property("event_controller._accept_events")
    _add_calibration_event = _component_property(
        "calibration_controller._add_calibration_event"
    )
    _apply_event = _component_property("event_controller._apply_event")
    _apply_recent_stop_status_hint = _component_property(
        "position_controller._apply_recent_stop_status_hint"
    )
    _async_build_non_encoder_position_calibration = _component_property(
        "calibration_controller._async_build_non_encoder_position_calibration"
    )
    _async_build_position_calibration = _component_property(
        "calibration_controller._async_build_position_calibration"
    )
    _async_build_time_position_calibration = _component_property(
        "calibration_controller._async_build_time_position_calibration"
    )
    _async_calibrate_target = _component_property(
        "calibration_controller._async_calibrate_target"
    )
    _async_calibrate_target_from_endpoint = _component_property(
        "calibration_controller._async_calibrate_target_from_endpoint"
    )
    _async_calibrate_target_from_endpoint_with_position_source = _component_property(
        "calibration_controller._async_calibrate_target_from_endpoint_with_position_source"
    )
    _async_calibrate_target_with_position_source = _component_property(
        "calibration_controller._async_calibrate_target_with_position_source"
    )
    _async_cancel_calibration = _component_property(
        "calibration_controller._async_cancel_calibration"
    )
    _async_cancel_position_simulation = _component_property(
        "position_controller._async_cancel_position_simulation"
    )
    _async_cancel_position_target = _component_property(
        "position_controller._async_cancel_position_target"
    )
    _async_cancel_post_command_refresh = _component_property(
        "position_controller._async_cancel_post_command_refresh"
    )
    _async_execute = _component_property("administration_controller._async_execute")
    _async_measure_full_travel = _component_property(
        "calibration_controller._async_measure_full_travel"
    )
    _async_measure_full_travel_time = _component_property(
        "calibration_controller._async_measure_full_travel_time"
    )
    _async_measure_full_travel_with_position_source = _component_property(
        "calibration_controller._async_measure_full_travel_with_position_source"
    )
    _async_move_to_end = _component_property(
        "calibration_controller._async_move_to_end"
    )
    _async_move_to_end_with_position_source = _component_property(
        "calibration_controller._async_move_to_end_with_position_source"
    )
    _async_move_to_end_without_encoder = _component_property(
        "calibration_controller._async_move_to_end_without_encoder"
    )
    _async_pause_before_next_calibration_command = _component_property(
        "calibration_controller._async_pause_before_next_calibration_command"
    )
    _async_prepare_mutation = _component_property(
        "administration_controller._async_prepare_mutation"
    )
    _async_read_motion_status = _component_property(
        "calibration_controller._async_read_motion_status"
    )
    _async_run_position_calibration = _component_property(
        "calibration_controller._async_run_position_calibration"
    )
    _async_run_position_simulation = _component_property(
        "position_controller._async_run_position_simulation"
    )
    _async_send_calibration_stop = _component_property(
        "calibration_controller._async_send_calibration_stop"
    )
    _async_start_post_command_refresh_after_delay = _component_property(
        "position_controller._async_start_post_command_refresh_after_delay"
    )
    _async_stop_at_position = _component_property(
        "position_controller._async_stop_at_position"
    )
    _async_wait_for_endpoint_after_stopped = _component_property(
        "calibration_controller._async_wait_for_endpoint_after_stopped"
    )
    _async_wait_for_settle = _component_property(
        "calibration_controller._async_wait_for_settle"
    )
    _build_calibration_report = _component_property(
        "calibration_controller._build_calibration_report"
    )
    _build_live_calibration_report = _component_property(
        "calibration_controller._build_live_calibration_report"
    )
    _calibrated_stop_delay_seconds = _component_property(
        "calibration_controller._calibrated_stop_delay_seconds"
    )
    _calibrated_stop_percent = _component_property(
        "calibration_controller._calibrated_stop_percent"
    )
    _calibrated_stop_raw = _component_property(
        "calibration_controller._calibrated_stop_raw"
    )
    _calibrated_travel_speed_percent_per_second = _component_property(
        "calibration_controller._calibrated_travel_speed_percent_per_second"
    )
    _calibration_attempt_successful = _component_property(
        "calibration_controller._calibration_attempt_successful"
    )
    _calibration_attempts_stable = _component_property(
        "calibration_controller._calibration_attempts_stable"
    )
    _calibration_events = _component_property(
        "calibration_controller._calibration_events"
    )
    _calibration_percent_for_status = _component_property(
        "calibration_controller._calibration_percent_for_status"
    )
    _calibration_position_source_for_status = _component_property(
        "calibration_controller._calibration_position_source_for_status"
    )
    _calibration_raw_for_status = _component_property(
        "calibration_controller._calibration_raw_for_status"
    )
    _calibration_source_bounds = _component_property(
        "calibration_controller._calibration_source_bounds"
    )
    _calibration_source_can_measure_targets = _component_property(
        "calibration_controller._calibration_source_can_measure_targets"
    )
    _calibration_source_quality = _component_property(
        "calibration_controller._calibration_source_quality"
    )
    _calibration_started_monotonic = _component_property(
        "calibration_controller._calibration_started_monotonic"
    )
    _calibration_state_machine = _component_property(
        "calibration_controller._calibration_state_machine"
    )
    _calibration_store = _component_property(
        "calibration_controller._calibration_store"
    )
    _calibration_success_count = _component_property(
        "calibration_controller._calibration_success_count"
    )
    _calibration_task = _component_property("calibration_controller._calibration_task")
    _check_abnormal_stop = _component_property("event_controller._check_abnormal_stop")
    _clamp_calibration_percent = _component_property(
        "calibration_controller._clamp_calibration_percent"
    )
    _clamp_raw = _component_property("position_controller._clamp_raw")
    _clear_position_simulation = _component_property(
        "position_controller._clear_position_simulation"
    )
    _command_age = _component_property("event_controller._command_age")
    _current_simulated_position = _component_property(
        "position_controller._current_simulated_position"
    )
    _endpoint_percent_from_state = _component_property(
        "calibration_controller._endpoint_percent_from_state"
    )
    _ensure_position_simulation_task = _component_property(
        "position_controller._ensure_position_simulation_task"
    )
    _event_client = _component_property("event_controller._event_client")
    _extend_post_command_fast_poll_window = _component_property(
        "position_controller._extend_post_command_fast_poll_window"
    )
    _finalize_live_scalar_bounds = _component_property(
        "calibration_controller._finalize_live_scalar_bounds"
    )
    _fire_abnormal_stop = _component_property("event_controller._fire_abnormal_stop")
    _format_calibration_report = _component_property(
        "calibration_controller._format_calibration_report"
    )
    _handle_raw_frame = _component_property("event_controller._handle_raw_frame")
    _handle_reader_failure = _component_property(
        "event_controller._handle_reader_failure"
    )
    _has_encoder_calibration_data = _component_property(
        "calibration_controller._has_encoder_calibration_data"
    )
    _hold_position_simulation = _component_property(
        "position_controller._hold_position_simulation"
    )
    _implausible_travel = _component_property("event_controller._implausible_travel")
    _init_calibration_state = _component_property(
        "calibration_controller._init_calibration_state"
    )
    _init_position_state = _component_property(
        "position_controller._init_position_state"
    )
    _invalid_calibration_sample = _component_property(
        "calibration_controller._invalid_calibration_sample"
    )
    _invalid_calibration_sample_with_position_source = _component_property(
        "calibration_controller._invalid_calibration_sample_with_position_source"
    )
    _is_at_endpoint = _component_property("calibration_controller._is_at_endpoint")
    _last_abnormal_fire = _component_property("event_controller._last_abnormal_fire")
    _last_known_position = _component_property(
        "position_controller.estimator.last_known_position"
    )
    _learn_live_scalar_bounds_from_travel = _component_property(
        "calibration_controller._learn_live_scalar_bounds_from_travel"
    )
    _learn_movement_speed = _component_property(
        "position_controller._learn_movement_speed"
    )
    _live_position_can_adjust_stop = _component_property(
        "position_controller._live_position_can_adjust_stop"
    )
    _live_scalar_raw_from_status = _component_property(
        "calibration_controller._live_scalar_raw_from_status"
    )
    _log_calibration_report = _component_property(
        "calibration_controller._log_calibration_report"
    )
    _maneuver_log_baseline = _component_property("diagnostic_state.maneuver_baseline")
    _motion_action_from_state = _component_property(
        "position_controller._motion_action_from_state"
    )
    _movement_profile = _component_property("position_controller.estimator.profile")
    _movement_profile_dirty = _component_property("position_controller.estimator.dirty")
    _movement_profile_store = _component_property("position_controller.estimator.store")
    _nhk_move = _component_property("event_controller._nhk_move")
    _nhk_obstacle_probed = _component_property("diagnostic_state.obstacle_probed")
    _normalize_live_position_event = _component_property(
        "position_controller._normalize_live_position_event"
    )
    _normalize_live_scalar_status = _component_property(
        "position_controller._normalize_live_scalar_status"
    )
    _normalize_live_scalar_value = _component_property(
        "position_controller._normalize_live_scalar_value"
    )
    _normalize_status_for_display = _component_property(
        "position_controller._normalize_status_for_display"
    )
    _notify_entities = _component_property("administration_controller._notify_entities")
    _observe_position_speed = _component_property(
        "position_controller._observe_position_speed"
    )
    _observed_position_speed_percent_per_second = _component_property(
        "position_controller.estimator.observed_speeds"
    )
    _obstruction_reconnect_count = _component_property(
        "event_controller._obstruction_reconnect_count"
    )
    _obstruction_state = _component_property("event_controller._obstruction_state")
    _open_close_read_as_stop = _component_property(
        "event_controller._open_close_read_as_stop"
    )
    _percent_for_raw = _component_property("position_controller._percent_for_raw")
    _position_observation = _component_property(
        "position_controller.estimator.observation"
    )
    _position_reached = _component_property("position_controller._position_reached")
    _position_reached_for_calibration = _component_property(
        "calibration_controller._position_reached_for_calibration"
    )
    _position_reporting_observed = _component_property(
        "position_controller.estimator.reporting_observed"
    )
    _position_simulation_action = _component_property(
        "position_controller.animation.action"
    )
    _position_simulation_anchor_monotonic = _component_property(
        "position_controller.animation.anchor_monotonic"
    )
    _position_simulation_anchor_position = _component_property(
        "position_controller.animation.anchor_position"
    )
    _position_simulation_confirmed_moving = _component_property(
        "position_controller.animation.confirmed_moving"
    )
    _position_simulation_deadline_monotonic = _component_property(
        "position_controller.animation.deadline_monotonic"
    )
    _position_simulation_speed = _component_property(
        "calibration_controller._position_simulation_speed"
    )
    _position_simulation_speed_curve = _component_property(
        "position_controller.animation.speed_curve"
    )
    _position_simulation_speed_percent_per_second = _component_property(
        "position_controller.animation.speed_percent_per_second"
    )
    _position_simulation_started_monotonic = _component_property(
        "position_controller.animation.started_monotonic"
    )
    _position_simulation_target_position = _component_property(
        "position_controller.animation.target_position"
    )
    _position_simulation_task = _component_property(
        "position_controller.animation.task"
    )
    _position_speed_curve = _component_property(
        "calibration_controller._position_speed_curve"
    )
    _position_target_task = _component_property(
        "position_controller.control.target_task"
    )
    _post_command_fast_poll_active = _component_property(
        "position_controller._post_command_fast_poll_active"
    )
    _post_command_fast_poll_until_monotonic = _component_property(
        "position_controller.control.fast_poll_until"
    )
    _post_command_refresh_delay_task = _component_property(
        "position_controller.control.refresh_task"
    )
    _raw_for_percent = _component_property("position_controller._raw_for_percent")
    _raw_for_percent_from_source = _component_property(
        "calibration_controller._raw_for_percent_from_source"
    )
    _raw_reached = _component_property("position_controller._raw_reached")
    _read_motion_status = _component_property(
        "calibration_controller._read_motion_status"
    )
    _rebase_position_simulation = _component_property(
        "position_controller._rebase_position_simulation"
    )
    _receive_raw_frame = _component_property("event_controller._receive_raw_frame")
    _receive_reader_failure = _component_property(
        "event_controller._receive_reader_failure"
    )
    _recent_command = _component_property("event_controller._recent_command")
    _recent_stop_command_monotonic = _component_property(
        "position_controller.control.recent_stop"
    )
    _recent_stop_started_from_motion = _component_property(
        "position_controller.control.stop_started_moving"
    )
    _record_calibration_live_sample = _component_property(
        "calibration_controller._record_calibration_live_sample"
    )
    _record_operation = _component_property(
        "administration_controller._record_operation"
    )
    _remaining_stop_delay_seconds = _component_property(
        "position_controller._remaining_stop_delay_seconds"
    )
    _remove_callbacks = _component_property("event_controller._remove_callbacks")
    _remove_event_callback = _component_property(
        "event_controller._remove_event_callback"
    )
    _remove_failure_callback = _component_property(
        "event_controller._remove_failure_callback"
    )
    _require_capability = _component_property(
        "administration_controller._require_capability"
    )
    _reset_calibration_live_source = _component_property(
        "calibration_controller._reset_calibration_live_source"
    )
    _reset_obstruction_observation = _component_property(
        "event_controller._reset_obstruction_observation"
    )
    _sampled_calibration_speed_percent_per_second = _component_property(
        "calibration_controller._sampled_calibration_speed_percent_per_second"
    )
    _schedule_movement_profile_save = _component_property(
        "position_controller._schedule_movement_profile_save"
    )
    _schedule_post_command_refresh = _component_property(
        "position_controller._schedule_post_command_refresh"
    )
    _select_calibration_sample = _component_property(
        "calibration_controller._select_calibration_sample"
    )
    _set_calibration_state = _component_property(
        "calibration_controller._set_calibration_state"
    )
    _start_position_simulation = _component_property(
        "position_controller._start_position_simulation"
    )
    _suppress_initial_obstruction = _component_property(
        "event_controller._suppress_initial_obstruction"
    )
    _sync_obstruction_connection = _component_property(
        "event_controller._sync_obstruction_connection"
    )
    _sync_position_simulation_from_status = _component_property(
        "position_controller._sync_position_simulation_from_status"
    )
    _t4_command_move = _component_property("event_controller._t4_command_move")
    _update_calibration_position_source = _component_property(
        "calibration_controller._update_calibration_position_source"
    )
    _update_interval_for_status = _component_property(
        "position_controller._update_interval_for_status"
    )
    _validate_position_bounds = _component_property(
        "position_controller._validate_position_bounds"
    )
    abnormal_stop_count = _component_property("event_controller.abnormal_stop_count")
    access_group_snapshot = _component_property(
        "administration_controller.access_group_snapshot"
    )
    administration_capability = _component_property(
        "administration_controller.administration_capability"
    )
    administration_history = _component_property(
        "administration_controller.administration_history"
    )
    advanced_diagnostic_code = _component_property(
        "event_controller.advanced_diagnostic_code"
    )
    async_load_calibration = _component_property(
        "calibration_controller.async_load_calibration"
    )
    async_load_movement_profile = _component_property(
        "position_controller.async_load_movement_profile"
    )
    async_reboot_interface = _component_property(
        "administration_controller.async_reboot_interface"
    )
    async_refresh_access_groups = _component_property(
        "administration_controller.async_refresh_access_groups"
    )
    async_refresh_controller_history = _component_property(
        "administration_controller.async_refresh_controller_history"
    )
    async_refresh_interface_logs = _component_property(
        "administration_controller.async_refresh_interface_logs"
    )
    async_save_movement_profile = _component_property(
        "position_controller.async_save_movement_profile"
    )
    async_set_position = _component_property("position_controller.async_set_position")
    async_start_position_calibration = _component_property(
        "calibration_controller.async_start_position_calibration"
    )
    async_sync_interface_time = _component_property(
        "administration_controller.async_sync_interface_time"
    )
    async_update_interface_name = _component_property(
        "administration_controller.async_update_interface_name"
    )
    basic_diagnostic_code = _component_property(
        "event_controller.basic_diagnostic_code"
    )
    bluebus_error_status = _component_property("event_controller.bluebus_error_status")
    calibration_cancel_reason = _component_property(
        "calibration_controller.calibration_cancel_reason"
    )
    calibration_cancel_stop_requested = _component_property(
        "calibration_controller.calibration_cancel_stop_requested"
    )
    calibration_cancel_stop_sent = _component_property(
        "calibration_controller.calibration_cancel_stop_sent"
    )
    calibration_last_error = _component_property(
        "calibration_controller.calibration_last_error"
    )
    calibration_profile = _component_property(
        "calibration_controller.calibration_profile"
    )
    calibration_quality = _component_property(
        "calibration_controller.calibration_quality"
    )
    calibration_report = _component_property(
        "calibration_controller.calibration_report"
    )
    calibration_report_attributes = _component_property(
        "calibration_controller.calibration_report_attributes"
    )
    calibration_report_summary = _component_property(
        "calibration_controller.calibration_report_summary"
    )
    calibration_state = _component_property("calibration_controller.calibration_state")
    calibration_updated_at = _component_property(
        "calibration_controller.calibration_updated_at"
    )
    connection_health = _component_property("connection_runtime.health")
    connection_state = _component_property("connection_runtime.state")
    controller_event_history = _component_property(
        "administration_controller.controller_event_history"
    )
    controller_event_history_error = _component_property(
        "administration_controller.controller_event_history_error"
    )
    controller_limits_changed = _component_property("diagnostic_state.limits_changed")
    display_position = _component_property("position_controller.display_position")
    display_position_estimated = _component_property(
        "position_controller.display_position_estimated"
    )
    ensure_registered = _component_property("event_controller.ensure_registered")
    event_battery_device_type = _component_property(
        "event_controller.event_battery_device_type"
    )
    event_battery_level = _component_property("event_controller.event_battery_level")
    event_history = _component_property("event_controller.event_history")
    event_sequence = _component_property("event_controller.event_sequence")
    event_stream_error = _component_property("event_controller.event_stream_error")
    event_stream_state = _component_property("event_controller.event_stream_state")
    interface_log_snapshot = _component_property(
        "administration_controller.interface_log_snapshot"
    )
    last_abnormal_stop = _component_property("event_controller.last_abnormal_stop")
    last_administration_operation = _component_property(
        "administration_controller.last_administration_operation"
    )
    last_command = _component_property("command_executor.state.last_command")
    last_command_latency_ms = _component_property(
        "command_executor.state.last_command_latency_ms"
    )
    last_command_monotonic = _component_property(
        "command_executor.state.last_command_monotonic"
    )
    last_command_result = _component_property("command_executor.state.last_result")
    last_error = _component_property("connection_runtime.last_error")
    last_event_at = _component_property("event_controller.last_event_at")
    last_event_cause = _component_property("event_controller.last_event_cause")
    last_reset_cause = _component_property("event_controller.last_reset_cause")
    last_reset_device_class = _component_property(
        "event_controller.last_reset_device_class"
    )
    last_successful_update = _component_property(
        "connection_runtime.last_successful_update"
    )
    last_t4_advanced_diagnostic = _component_property(
        "event_controller.last_t4_advanced_diagnostic"
    )
    last_t4_basic_diagnostic = _component_property(
        "event_controller.last_t4_basic_diagnostic"
    )
    last_t4_cause = _component_property("event_controller.last_t4_cause")
    last_t4_change_state = _component_property("event_controller.last_t4_change_state")
    last_t4_command = _component_property("event_controller.last_t4_command")
    latest_event = _component_property("event_controller.latest_event")
    malformed_protocol_event_count = _component_property(
        "event_controller.malformed_protocol_event_count"
    )
    manoeuvre_average_current = _component_property(
        "event_controller.manoeuvre_average_current"
    )
    mark_connected = _component_property("event_controller.mark_connected")
    mark_reconnecting = _component_property("event_controller.mark_reconnecting")
    movement_speed_profile = _component_property(
        "position_controller.movement_speed_profile"
    )
    nhk_obstacle_seed = _component_property("diagnostic_state.obstacle_seed")
    nhk_status_fields = _component_property("diagnostic_state.nhk_fields")
    nhk_status_read_at = _component_property("diagnostic_state.nhk_read_at")
    observed_position_speed_percent_per_second = _component_property(
        "position_controller.observed_position_speed_percent_per_second"
    )
    position_confidence = _component_property("position_controller.position_confidence")
    position_reporting_observed = _component_property(
        "position_controller.position_reporting_observed"
    )
    position_simulation_action = _component_property(
        "position_controller.position_simulation_action"
    )
    position_simulation_speed_percent_per_second = _component_property(
        "position_controller.position_simulation_speed_percent_per_second"
    )
    position_snapshot = _component_property("position_controller.position_snapshot")
    position_source = _component_property("position_controller.position_source")
    protocol_event_count = _component_property("event_controller.protocol_event_count")
    reconcile_polled_obstacle = _component_property(
        "event_controller.reconcile_polled_obstacle"
    )
    report_abnormal = _component_property("event_controller.report_abnormal")
    shutdown = _component_property("event_controller.shutdown")
    state_source = _component_property("position_controller.state_source")
    _median_time_travel_duration_ms = _component_property(
        "calibration_controller._median_time_travel_duration_ms"
    )
    _select_time_travel_sample = _component_property(
        "calibration_controller._select_time_travel_sample"
    )
    _calibration_attempt_valid = _component_property(
        "calibration_controller._calibration_attempt_valid"
    )
    _calibration_attempt_abs_error = _component_property(
        "calibration_controller._calibration_attempt_abs_error"
    )
    _interpolate_stop_percent = _component_property(
        "calibration_controller._interpolate_stop_percent"
    )
