"""Descriptions for diagnostic states; never used by the decision logic."""
DESCRIPTIONS = {'advice_disabled': 'The advisor is disabled; final reduction is zero.',
 'reduction_methods_disabled': 'Both reduction methods are disabled.',
 'price_disabled': 'Price reduction is disabled.',
 'price_inputs_unavailable': 'The enable controls are not available yet.',
 'price_level_0': 'The heat-cost premium is at most 0.005 EUR/kWh heat; no price reduction is '
                  'requested.',
 'price_level_1': 'The heat-cost premium is above 0.005 and at most 0.010 EUR/kWh heat; price '
                  'requests 1°C.',
 'price_level_2': 'The heat-cost premium is above 0.010 and at most 0.020 EUR/kWh heat; price '
                  'requests 2°C.',
 'price_level_3': 'The heat-cost premium is above 0.020 and at most 0.030 EUR/kWh heat; price '
                  'requests 3°C.',
 'price_level_4': 'The heat-cost premium exceeds 0.030 EUR/kWh heat; price requests 4°C.',
 'forecast_unavailable': 'Required forecast inputs are missing or unsupported. See the forecast '
                         'problems below.',
 'forecast_stale': 'The forecast has expired; a valid new forecast is required.',
 'forecast_missing': 'No usable forecast is available yet.',
 'DHW_mode': 'Domestic hot water is active; the advisor requests a return towards zero reduction.',
 'valve_state_unknown': 'The Room/DHW valve state is not available.',
 'invalid_water_measurements': 'Water or heating measurements are missing or invalid.',
 'persistent_room_shortfall': 'Shared room comfort protection requests zero reduction; a room is '
                              'persistently too far below target, or room data is missing.',
 'little_estimated_capacity_margin': 'Predicted heat pump heat demand is at least 8 kW; capacity '
                                     'protection requests zero reduction.',
 'defrost_interval_too_short': 'Two defrosts started closer together than the configured interval. '
                               'Frequent defrost protection requests zero reduction.',
 'hydraulic_history_hold': 'The previous reduction is held briefly while hydraulic history '
                           'recovers.',
 'hydraulic_hold_expired': 'The bounded hydraulic hold has expired; the advisor requests a return '
                           'towards zero reduction.',
 'hydraulic_history_warming_up': 'Hydraulic history is not ready; the advisor requests zero '
                                 'reduction.',
 'outdoor_temperature_invalid': 'The heat pump outside reading is missing or invalid; a new target '
                                'cannot be calculated.',
 'efficiency_disabled': 'Efficiency trim is disabled.',
 'efficiency_comfort_recovery': 'Shared room comfort protection requests zero efficiency '
                                'reduction.',
 'efficiency_capacity_recovery': 'Predicted heat pump demand is at least 8 kW; efficiency '
                                 'reduction is being recovered.',
 'efficiency_defrost_hold': 'Efficiency holds its addition while frequent defrost protection '
                            'limits the final reduction.',
 'efficiency_defrost_pause': 'Efficiency holds its addition during defrost and the brief return '
                             'to heating. Recent eligible readings are kept.',
 'efficiency_settings_invalid': 'Settings must satisfy low gap < high gap and room margin < '
                                'recovery shortfall.',
 'efficiency_inputs_unavailable': 'Required room, water or outside-temperature inputs are missing '
                                  'or invalid.',
 'efficiency_heating_paused': 'There is no eligible active space heating for hydraulic '
                              'observation; the existing efficiency request is held.',
 'efficiency_observations_warming_up': 'Eligible hydraulic history is not ready; the existing '
                                       'efficiency request is held.',
 'efficiency_waiting_for_forecast': 'Price reduction is enabled and its forecast is unavailable; '
                                    'efficiency holds its current request.',
 'efficiency_room_shortfall_recovery': 'Room shortfall has reached the recovery threshold. Efficiency '
                                       'requests one degree less reduction.',
 'efficiency_room_shortfall_no_addition': 'Room shortfall has reached the Efficiency recovery threshold. '
                                          'There is no extra Efficiency reduction to withdraw.',
 'efficiency_low_gap_recovery': 'The current return gap is at or below the low limit. Efficiency '
                                'requests one degree less reduction.',
 'efficiency_low_gap_no_addition': 'The return gap is at or below the low limit. There is no extra '
                                   'Efficiency reduction to withdraw.',
 'efficiency_waiting_for_reduction': 'Waiting for the advised reduction to reach the pending request '
                                      'before adding trim.',
 'efficiency_waiting_for_shift': 'Waiting for the heat pump to report the advised shift.',
 'efficiency_shift_not_applied': 'The reported shift still differs from Final advice. Efficiency holds its addition.',
 'efficiency_shift_readback_unavailable': 'The reported shift is unavailable. Efficiency holds its addition.',
 'target_floor_limits_reduction': 'The configured minimum target limits the requested reduction.',
 'efficiency_room_margin_insufficient': 'Room shortfall is above the efficiency margin or is '
                                        'increasing too quickly; no further reduction is '
                                        'requested.',
 'efficiency_margin_inconclusive': 'The 30-minute minimum eligible return gap is below the high '
                                   'limit. Efficiency holds its current request.',
 'efficiency_maximum_reduction': 'The efficiency request has reached the 4°C maximum.',
 'efficiency_temperature_floor': 'Another reduction would not lower the target because of the 27°C '
                                 'floor.',
 'efficiency_margin_available': 'Rooms and hydraulic observations allow another degree of '
                                'efficiency reduction.',
 'efficiency_hold': 'The current efficiency request is held.',
 'unknown': 'Waiting for a recorded state.',
 'unavailable': 'This state is temporarily unavailable.',
 'startup_waiting_for_decision': 'Waiting for the next quarter-hour decision.',
 'startup_waiting_for_observations': 'Waiting for valid heating observations.'}


def describe(reason):
    text=str(reason or "unknown")
    code=text.split(";")[0].strip()
    if code.startswith("forecast_unavailable:"):
        message="Required forecast input: "+code.split(":",1)[1].strip()
    elif code.startswith("room_data_missing:"):
        message="Room data is missing: "+code.split(":",1)[1].strip()
    elif code.startswith("efficiency_level_"):
        message="Price and Efficiency together request "+code.split("_")[-1]+"°C of reduction."
    else:
        message=DESCRIPTIONS.get(code,"See the exact diagnostic state for details.")
    if "waiting for 25-minute minimum interval" in text:
        message+=" The next step is waiting for the minimum interval between changes."
    elif "moving one degree per decision" in text:
        message+=" The advised reduction is moving by one degree per eligible decision."
    return message

# Configurable equipment limits must not be described as fixed model values.
for level in range(5):
    DESCRIPTIONS[f"price_level_{level}"] = f"Price requests {level}°C before shared safeguards and target limits."
DESCRIPTIONS.update(little_estimated_capacity_margin="Predicted heat demand reaches the configured capacity threshold; zero reduction is requested.", efficiency_capacity_recovery="Predicted heat demand reaches the configured capacity threshold; efficiency trim is being recovered.", efficiency_maximum_reduction="The configured maximum efficiency reduction has been reached.", efficiency_temperature_floor="The configured target-temperature floor prevents a further reduction.")
