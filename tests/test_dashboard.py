from custom_components.heatpump_supplytemp_optimizer.dashboard import build_dashboard
from custom_components.heatpump_supplytemp_optimizer.sensor import SENSORS
from custom_components.heatpump_supplytemp_optimizer.binary_sensor import DEFINITIONS
from custom_components.heatpump_supplytemp_optimizer.const import SETTINGS


def generated(count):
    rooms=[{'id':f'room_{i}','name':f'Room {i}'} for i in range(count)]
    keys=list(SENSORS)+list(DEFINITIONS)+list(SETTINGS)+['enabled','price_enabled','efficiency_enabled','refresh_forecast','export_dashboard']
    keys += [prefix+r['id'] for r in rooms for prefix in
             ['room_shortfall_','room_limited_shortfall_','room_allowance_','room_max_shortfall_rise_']]
    return build_dashboard({k:'sensor.test_'+k for k in keys},rooms)


def test_large_homes_split_native_room_graphs_at_eight_entities():
    d=generated(20)
    comfort=next(v for v in d['views'] if v['path']=='comfort')
    for title in ('Room shortfalls after allowances', 'Room shortfalls used by optimizer'):
        charts=[c for s in comfort['sections'] for c in s['cards'] if c.get('title','').startswith(title)]
        assert [len(c['entities']) for c in charts]==[8,8,4]
        assert len({row['entity'] for c in charts for row in c['entities']})==20


def test_advice_shows_stages_in_decision_order_and_charts_only_totals():
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
        "sensor.test_decision_price_reduction", "sensor.test_combined_reduction",
        "sensor.test_protected_reduction", "sensor.test_effective_reduction",
    ]


def test_forecast_only_uses_custom_graphs_and_gap_axes_remain_fixed():
    d=generated(2)
    for view in d['views']:
        for section in view['sections']:
            for card in section['cards']:
                if view['path']!='forecast':assert not card['type'].startswith('custom:')
                if card.get('title','').startswith('Return gap ·'):
                    assert (card['min_y_axis'],card['max_y_axis'],card['fit_y_data'])==(-10,10,False)
