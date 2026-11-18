from custom_components.heatpump_supplytemp_optimizer.dashboard import build_dashboard
from custom_components.heatpump_supplytemp_optimizer.sensor import SENSORS
from custom_components.heatpump_supplytemp_optimizer.binary_sensor import DEFINITIONS


def generated(count):
    rooms=[{'id':f'room_{i}','name':f'Room {i}'} for i in range(count)]
    keys=list(SENSORS)+list(DEFINITIONS)+['enabled','price_enabled','efficiency_enabled','gap_high','gap_low','room_margin','room_recovery','refresh_forecast','export_dashboard']
    keys += [prefix+r['id'] for r in rooms for prefix in ['room_shortfall_','room_allowance_']]
    return build_dashboard({k:'sensor.test_'+k for k in keys},rooms)


def test_large_homes_split_native_room_graphs_at_eight_entities():
    d=generated(20)
    comfort=next(v for v in d['views'] if v['path']=='comfort')
    charts=[c for s in comfort['sections'] for c in s['cards'] if c.get('title','').startswith('Room shortfalls')]
    assert [len(c['entities']) for c in charts]==[8,8,4]
    assert len({row['entity'] for c in charts for row in c['entities']})==20


def test_forecast_only_uses_custom_graphs_and_gap_axes_remain_fixed():
    d=generated(2)
    for view in d['views']:
        for section in view['sections']:
            for card in section['cards']:
                if view['path']!='forecast':assert not card['type'].startswith('custom:')
                if card.get('title','').startswith('Return gap ·'):
                    assert (card['min_y_axis'],card['max_y_axis'],card['fit_y_data'])==(-10,10,False)
