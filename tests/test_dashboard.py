from custom_components.heatpump_supplytemp_optimizer.dashboard import build_dashboard
from custom_components.heatpump_supplytemp_optimizer.sensor import SENSORS
from custom_components.heatpump_supplytemp_optimizer.binary_sensor import DEFINITIONS
from custom_components.heatpump_supplytemp_optimizer.const import SETTINGS, SWITCHES


def generated(count, *, excluded=(), defrost_entity=None):
    rooms=[{'id':f'room_{i}','name':f'Room {i}','enabled':i not in excluded} for i in range(count)]
    keys=list(SENSORS)+list(DEFINITIONS)+list(SETTINGS)+list(SWITCHES)+['refresh_forecast','export_dashboard']
    keys += [prefix+r['id'] for r in rooms for prefix in
             ['room_shortfall_','room_limited_shortfall_','room_allowance_','room_max_shortfall_rise_']]
    return build_dashboard({k:'sensor.test_'+k for k in keys},rooms,defrost_entity=defrost_entity)


def test_large_homes_split_native_room_graphs_at_eight_entities():
    d=generated(20)
    comfort=next(v for v in d['views'] if v['path']=='comfort')
    for title in ('Room shortfalls after allowances', 'Room shortfalls used by optimizer'):
        charts=[c for s in comfort['sections'] for c in s['cards'] if c.get('title','').startswith(title)]
        assert [len(c['entities']) for c in charts]==[8,8,4]
        assert len({row['entity'] for c in charts for row in c['entities']})==20


def test_advice_colors_decision_tiles_and_history_consistently():
    advice = next(v for v in generated(1)["views"] if v["path"] == "advice")
    stages = advice["sections"][0]["cards"]
    assert [c["entity"] for c in stages if c["type"] == "tile"] == [
        "sensor.test_decision_price_reduction", "sensor.test_efficiency_addition",
        "sensor.test_combined_reduction", "sensor.test_protected_reduction",
        "sensor.test_effective_reduction",
    ]
    history = next(c for s in advice["sections"] for c in s["cards"]
                   if c.get("title") == "Temperature reductions")
    assert [row["entity"] for row in history["entities"]] == [
        "sensor.test_decision_price_reduction", "sensor.test_efficiency_addition", "sensor.test_combined_reduction",
        "sensor.test_protected_reduction", "sensor.test_effective_reduction",
    ]
    expected = ["#8b5cf6", "#22a447", "#8b949e", "#e54848", "#eabf12"]
    assert [c["color"] for c in stages if c["type"] == "tile"] == expected
    assert [row["color"] for row in history["entities"]] == expected


def test_only_forecast_and_savings_use_custom_graphs_and_gap_axes_remain_fixed():
    d=generated(2)
    for view in d['views']:
        for section in view['sections']:
            for card in section['cards']:
                if view['path'] not in ('forecast','savings'):assert not card['type'].startswith('custom:')
                if card.get('title','').startswith('Return gap ·'):
                    assert (card['min_y_axis'],card['max_y_axis'],card['fit_y_data'])==(-10,10,False)

def test_review_views_distinguish_live_inputs_and_decision_values():
    views = {v["path"]: v for v in generated(3, excluded=(1,), defrost_entity="binary_sensor.hp_defrost")["views"]}
    targets = views["advice"]["sections"][1]["cards"]
    assert targets[1]["entity"] == "sensor.test_decision_baseline"
    price = views["price"]["sections"][0]["cards"]
    assert [c["name"] for c in price if c["type"] == "tile"][:2] == ["Live heat-cost premium", "Live price request"]
    charts = views["comfort"]["sections"][-1]["cards"]
    used = next(c for c in charts if c["type"] == "history-graph")
    assert [r["name"] for r in used["entities"]] == ["Room 0", "Room 2"]
    timeline, compressor = views["efficiency"]["sections"][-1]["cards"][1:3]
    assert [r["entity"] for r in timeline["entities"]] == [
        "sensor.test_heating_mode", "binary_sensor.hp_defrost", "sensor.test_shift_confirmation",
        "sensor.test_eligible_heating", "sensor.test_observations_ready"]
    assert compressor["entities"][0]["entity"] == "sensor.test_compressor_frequency"
