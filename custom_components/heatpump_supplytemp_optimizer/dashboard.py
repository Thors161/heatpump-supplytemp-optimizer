"""Generate a dashboard from registered integration entity IDs."""
P = {"price":"#8b5cf6", "efficiency":"#0d9488", "protected":"#dc6676",
     "final":"#f59e0b", "original":"#8b949e", "actual":"#3b82f6"}


def build_dashboard(entities, rooms, *, defrost_entity=None):
    if defrost_entity:
        entities = {**entities, "defrost_activity": defrost_entity}
    def entity(key):
        return entities[key]
    def heading(title):
        return dict(type="heading", heading=title, heading_style="title")
    def tile(key, name, color=None):
        result = dict(type="tile", entity=entity(key), name=name, grid_options={"columns":6,"rows":2})
        if color:
            result["color"] = color
        if key in ["comfort_shortfall", "defrost_protection"]:
            result["state_content"] = ["status"]
        if key in ["enabled", "price_enabled", "efficiency_enabled", "comfort_enabled", "defrost_enabled"]:
            result["features"] = [{"type":"toggle"}]
        return result
    def text(content):
        return dict(type="markdown", content=content)
    def section(title, *cards):
        return dict(type="grid", cards=[heading(title), *cards])
    def view(title, path, sections):
        return dict(title=title, path=path, type="sections", max_columns=2, sections=sections)
    def history(title, rows, **extra):
        return dict(type="history-graph", title=title, hours_to_show=24,
            entities=[dict(entity=entity(key),name=name,**({"color":color[0]} if color else {})) for key,name,*color in rows],
            grid_options={"columns":"full","rows":5}, **extra)
    def reasons(*rows):
        return text("\n\n---\n\n".join("**"+name+":** `{{ states('"+entity(key)+"') }}`\n\n{{ state_attr('"+entity(key)+"','description') or 'Waiting for a diagnostic state.' }}" for key,name in rows))
    def editable(title, rows):
        return dict(type="entities", title=title, show_header_toggle=False,
                    entities=[dict(entity=entity(key),name=name) for key,name in rows])
    decision_card = reasons(("decision", "Decision"))
    decision_card["content"] += """
{% set at = state_attr('DECISION', 'decision_at') %}
{% set next = state_attr('DECISION', 'next_step_at') %}
{% if at is number %}Evaluated {{ at | timestamp_custom('%a %H:%M', true) }}.{% endif %}
{% if next is number %}Earliest scheduled step if still requested: {{ next | timestamp_custom('%a %H:%M', true) }}.{% endif %}""".replace("DECISION", entity("decision"))

    def active_detail(key, content):
        return dict(type="conditional", conditions=[dict(condition="state", entity=entity(key), state="on")],
                    card=text(content.replace("PROTECTION", entity(key))))

    timeline = [("heating_mode", "Heating mode")]
    if defrost_entity:
        timeline.append(("defrost_activity", "Defrost activity"))
    timeline.extend([("shift_confirmation", "Shift confirmation"), ("eligible_heating", "Eligible heating"),
                     ("observations_ready", "Efficiency ready")])
    controls = [tile("enabled","Advisor"),tile("price_enabled","Price reduction",P["price"]),tile("efficiency_enabled","Efficiency trim",P["efficiency"])]
    advice = view("Advice", "advice", [
        section("Last decision", tile("decision_price_reduction","Price at decision",P["price"]),
            tile("efficiency_addition","Efficiency addition",P["efficiency"]),
            tile("combined_reduction","Combined request",P["efficiency"]),
            tile("protected_reduction","After protections",P["protected"]),
            tile("effective_reduction","Final advice now",P["final"]), decision_card),
        section("Heating targets", tile("decision_baseline","Baseline at decision",P["original"]),
            tile("target_temperature","Advised target",P["final"]),tile("actual_target","Actual heat pump",P["actual"]),
            tile("forecast","Forecast")),
        section("Shift confirmation", tile("advised_shift","Advised shift",P["final"]),
            tile("reported_shift","Reported shift",P["actual"]), tile("shift_confirmation","Confirmation")),
        section("Controls & decisions", *controls, reasons(("price_state","Live Price"),("efficiency_reason","Efficiency"))),
        section("Shared protections now",
            tile("comfort_shortfall","Comfort protection"), tile("estimated_capacity_reached","Estimated capacity reached"),
            tile("defrost_protection","Frequent defrost protection"),
            tile("hydraulics_ready","Hydraulic history ready"),
            tile("heating_mode","Heating mode"), tile("local_outdoor","Local outside temperature"),
            active_detail("estimated_capacity_reached",
                "Modeled heat-pump demand **{{ state_attr('PROTECTION', 'demand_kw') | round(2) }} kW** · "
                "Capacity limit **{{ state_attr('PROTECTION', 'capacity_kw') }} kW**"),
            active_detail("defrost_protection",
                "Last two defrost starts **{{ state_attr('PROTECTION', 'interval_minutes') | round(1) }} min** apart · "
                "Minimum interval **{{ state_attr('PROTECTION', 'minimum_minutes') }} min**"),
            history("Shared protection history", [
                ("comfort_shortfall","Comfort protection"), ("estimated_capacity_reached","Estimated capacity reached"),
                ("defrost_protection","Frequent defrost protection"),
                ("hydraulics_ready","Hydraulic history ready"),
                ("heating_mode","Heating mode")])),
        section("Reduction history",history("Temperature reductions",[
            ("decision_price_reduction","Price request",P["price"]),("combined_reduction","Combined request",P["efficiency"]),
            ("protected_reduction","After protections",P["protected"]),("effective_reduction","Final advice",P["final"])])),
        section("Target history",history("Heating targets",[("decision_baseline","Baseline at decision",P["original"]),("target_temperature","Advised target",P["final"]),("actual_target","Actual heat pump",P["actual"])])),
        section("State history",history("Decision states",[("decision","Decision"),("price_state","Price"),("efficiency_reason","Efficiency")]),
            history("Controls & forecast",[("enabled","Advisor"),("price_enabled","Price"),("efficiency_enabled","Efficiency"),("forecast","Forecast")]))])
    price = view("Price","price",[
        section("Price decision",tile("heat_cost_premium_6h","Live heat-cost premium"),tile("price_reduction","Live price request",P["price"]),
            tile("current_heat_cost","Heat cost now"),tile("reference_heat_cost","Next 6h median"),tile("effective_reduction","Final advice",P["final"]),
            reasons(("price_state","Live Price"),("decision","Last decision"))),
        section("Heat costs",history("Estimated heat costs · EUR/kWh heat",[("current_heat_cost","Heat cost now",P["price"]),("reference_heat_cost","Next 6h median",P["efficiency"]),("original_curve_heat_cost","Original curve",P["original"])]),
            tile("efficiency_heat_cost_difference","Estimated efficiency difference"),text("Cost differences are model estimates per kWh of heat, not measured total savings.")),
        section("Electricity & solar",history("Electricity value · EUR/kWh",[("import_price","Import price",P["price"]),("export_price","Export value",P["original"])]),
            history("PV production · kW",[("pv_power","PV used by advisor","#e4b32c")]),tile("other_load","Other household load")),
        section("COP & calculation inputs",history("Predicted & measured COP",[("original_curve_cop","Original curve",P["original"]),("predicted_cop","Efficiency curve",P["efficiency"]),("measured_cop_30m","Measured, 30 min",P["actual"])]),
            tile("efficiency_curve_temperature","Efficiency curve"),
            text("{% set f=state_attr('"+entity("forecast")+"','result') or {} %}**Forecast problems:** {{ f.get('problems',[])|join(', ') or 'none' }}\n\n**PV source:** {{ f.get('pv_source','unknown') }}"))])
    def room_graphs(title, prefix, *, included_only=False):
        colors = ["#f59e0b", "#3b82f6", "#a78bfa", "#ec4899", "#0d9488"]
        rows = [(prefix+room["id"], room["name"], colors[i % len(colors)]) for i, room in enumerate(rooms)
                if not included_only or room.get("enabled", True)]
        # Native history cards support at most eight entities.
        return [history(title+(f" · {i//8+1}" if len(rows)>8 else ""), rows[i:i+8])
                for i in range(0, len(rows), 8)]
    comfort = view("Comfort", "comfort", [
        section("Room comfort",
            tile("comfort_shortfall","Comfort protection"), tile("limited_room_deficit","Maximum shortfall used"),
            text("""{% set maximum = states('SHORTFALL') %}
{% set rooms = state_attr('SHORTFALL', 'limiting_rooms') or [] %}
{% if not is_number(maximum) %}Waiting for included room readings.
{% elif rooms %}**Largest limited shortfall:** {{ rooms | join(', ') | e }} · {{ maximum | float | round(2) }} °C
{% else %}Included rooms have no shortfall.{% endif %}""".replace("SHORTFALL", entity("limited_room_deficit"))),
            text("When enabled, protection requests zero reduction after 30 minutes at or above the configured threshold of {{ states('"+entity("comfort_threshold")+"') }} °C. It clears after 10 minutes below that threshold."),
            history("Maximum room shortfall", [
                ("max_room_deficit","Raw",P["original"]), ("adjusted_room_deficit","After allowances","#dc6676"),
                ("limited_room_deficit","Used by optimizer",P["actual"])])),
        section("Adjusted shortfalls", *room_graphs("Room shortfalls after allowances", "room_shortfall_")),
        section("Shortfalls used by optimizer", *room_graphs("Room shortfalls used by optimizer", "room_limited_shortfall_", included_only=True),
            text("Configured rise limits slow increases. Decreases apply immediately. Off rooms show zero and missing readings appear as gaps."))])
    efficiency=view("Efficiency","efficiency",[
        section("Efficiency decision",tile("efficiency_addition","Efficiency addition",P["efficiency"]),tile("limited_room_deficit","Shortfall used by optimizer"),
            reasons(("efficiency_reason","Efficiency"),("decision","Shared decision"))),
        section("Return temperatures & gap",history("Return temperatures",[("primary_return","Primary return",P["actual"]),("secondary_return","Secondary return",P["efficiency"])]),
            history("Return gap · °C",[("return_gap","Current gap",P["actual"]),("efficiency_gap_min_30m","Eligible minimum",P["efficiency"]),("gap_high","High gap",P["final"]),("gap_low","Low gap",P["original"])],min_y_axis=-10,max_y_axis=10,fit_y_data=False)),
        section("Primary flow & trends",history("Primary flow · L/min",[("primary_flow","Primary flow",P["actual"])]),
            history("Return-gap change · °C",[("return_gap_change_30m","Change, 30 min")]),history("Shortfall change · °C",[("room_deficit_change_30m","Change, 30 min")])),
        section("Observation readiness",history("Heating and Efficiency · 24 hours", timeline),
            history("Compressor frequency · 24 hours", [("compressor_frequency","Compressor frequency")]),
            tile("heat_power","Heat output"))])

    def forecast_chart(title, unit, series):
        lines=[]
        for key,name,color in series:
            generator="""const f=entity.attributes.result||{};
if(!f.complete || !Number.isFinite(Number(f.valid_until)) || Date.now()/1000>=Number(f.valid_until)) return [];
const points=(f.quarters||[]).filter(q=>q.FIELD!==null && q.FIELD!==undefined)
 .map(q=>[Number(q.start)*1000,Number(q.FIELD)])
 .filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1])).sort((a,b)=>a[0]-b[0]);
if(!points.length)return [];
const lo=start.getTime(),hi=end.getTime();
const past=points.filter(p=>p[0]<=lo), result=points.filter(p=>p[0]>=lo&&p[0]<=hi);
if(past.length&&(!result.length||result[0][0]>lo)&&lo<points.at(-1)[0]+900000)
 result.unshift([lo,past.at(-1)[1]]);
const coverageEnd=Math.min(hi,points.at(-1)[0]+900000);
if(result.length&&coverageEnd>result.at(-1)[0])result.push([coverageEnd,points.filter(p=>p[0]<=coverageEnd).at(-1)[1]]);
return result;""".replace("FIELD",key)
            lines.append(dict(entity=entity("forecast"),name=name,color=color,type="line",curve="stepline",stroke_width=2,
                unit=unit,float_precision=2 if unit.startswith("EUR/") else 1,extend_to=False,
                data_generator=generator,show={"in_header":"after_now","legend_value":False}))
        return dict(type="custom:apexcharts-card",section_mode=True,graph_span="6h",span={"start":"minute"},
            header={"show":True,"title":title,"show_states":True,"colorize_states":True},now={"show":True,"label":"Now"},
            apex_config={"chart":{"height":280},"yaxis":{"decimalsInFloat":2 if unit.startswith("EUR/") else 1}},series=lines,
            grid_options={"columns":"full","rows":6})
    table="""{% set f=state_attr('FORECAST','result') or {} %}
**Status:** {{ states('FORECAST') }}
**Problems:** {{ f.get('problems',[])|join(', ') or 'none' }}
**Next six hours:** {{ f.get('house_heat_energy_6h_kwh')|round(1) if is_number(f.get('house_heat_energy_6h_kwh')) else '—' }} kWh house heat;
{{ f.get('heatpump_heat_energy_6h_kwh')|round(1) if is_number(f.get('heatpump_heat_energy_6h_kwh')) else '—' }} kWh heat-pump share.
{% macro cell(v,n) %}{{ ('%.'~n~'f')|format(v|float) if is_number(v) else '—' }}{% endmacro %}
<table><thead><tr><th>Time</th><th>Import<br>EUR/kWh</th><th>PV<br>kW</th><th>Outside<br>°C</th><th>House<br>kW</th><th>Heat pump<br>kW</th><th>COP</th><th>Heat cost<br>EUR/kWh</th></tr></thead><tbody>
{% for q in f.get('quarters',[]) %}<tr><td>{{ q.start|timestamp_custom('%H:%M',true) }}</td><td>{{ cell(q.import_price,2) }}</td><td>{{ cell(q.pv_kw,1) }}</td><td>{{ cell(q.outdoor,1) }}</td><td>{{ cell(q.house_heat_demand_kw,1) }}</td><td>{{ cell(q.heat_demand_kw,1) }}</td><td>{{ cell(q.cop,1) }}</td><td>{{ cell(q.heat_cost,2) }}</td></tr>{% endfor %}
</tbody></table>""".replace("FORECAST",entity("forecast"))
    forecast=view("Forecast","forecast",[section("Quarter-hour forecast",
        forecast_chart("Heat demand · next six hours","kW",[("house_heat_demand_kw","House demand",P["efficiency"]),("heat_demand_kw","Heat-pump share",P["actual"]),("ventilation_contribution_kw","Ventilation contribution",P["final"])]),
        forecast_chart("PV production","kW",[("pv_kw","PV production","#e4b32c")]),
        forecast_chart("Outside temperature","°C",[("outdoor","Outside temperature",P["actual"])]),
        forecast_chart("Import price","EUR/kWh",[("import_price","Import price",P["price"])]),
        forecast_chart("Estimated heat cost","EUR/kWh heat",[("heat_cost","Heat cost","#dc6676")]),
        forecast_chart("Predicted COP","COP",[("cop","COP",P["efficiency"])]),text(table))])
    def statistics(title, rows):
        return dict(type="statistics-graph",title=title,days_to_show=7,period="hour",chart_type="line",stat_types=["mean"],
                    entities=[dict(entity=entity(k),name=n) for k,n in rows],grid_options={"columns":"full","rows":5})
    past=view("History","history",[section("Matched space heating",statistics("Space-heating power · hourly",[("predicted_heating_power","Predicted heat"),("measured_heating_power","Measured matched heat")]),
        statistics("Targets · seven days",[("decision_baseline","Baseline at decision"),("target_temperature","Advised target")]),
        statistics("COP · seven days",[("predicted_cop","Predicted COP"),("measured_cop_30m","Measured COP")]),
        text("Power comparisons use predictions made before each complete quarter. DHW, startup partial quarters and invalid gaps are excluded. Predicted/measured power is thermal kW, not electrical power or measured savings."))])
    settings=view("Settings","settings",[
        section("General",*controls,tile("refresh_forecast","Refresh forecast"),tile("export_dashboard","Export dashboard")),
        section("Room comfort · both methods",tile("comfort_enabled","Enable comfort protection"),
            editable("Comfort protection threshold",[("comfort_threshold","Comfort shortfall threshold")]),
            editable("Extra allowed shortfall",[("room_allowance_"+r["id"],r["name"]) for r in rooms]),
            editable("Maximum shortfall rise",[("room_max_shortfall_rise_"+r["id"],r["name"]) for r in rooms]),
            text("The allowance is subtracted before limiting the rise. Set a room's rise limit to 0 to disable it. A limit of 1°C/h allows up to 0.5°C of increase in 30 minutes. The demand model uses actual room temperatures.")),
        section("Efficiency · adjustable",editable("Hydraulic & comfort thresholds",[("gap_high","High gap"),("gap_low","Low gap"),("room_margin","Room margin for more trim"),("room_recovery","Room shortfall for recovery")]),
            editable("Shift readback",[("shift_confirmation_grace","Shift confirmation grace period")]),
            text("The 30-minute minimum eligible gap must reach the high limit for more trim. A current gap at or below the low limit backs off efficiency trim. Require low < high and room margin < recovery.")),
        section("Frequent defrost protection",tile("defrost_enabled","Enable frequent defrost protection"),
            editable("Defrost timing",[("minimum_defrost_interval","Minimum time between starts")]),
            text("With a defrost sensor configured, two starts less than this many minutes apart request zero reduction. The protection clears after that many minutes without a new start. Set 0 to disable it.")),
        section("Price · fixed thresholds",text("Comparison: current heat cost minus the next-six-hour median.\n\n| Premium EUR/kWh heat | Request |\n|:--|--:|\n| ≤0.005 | 0°C |\n| >0.005 to 0.010 | 1°C |\n| >0.010 to 0.020 | 2°C |\n| >0.020 to 0.030 | 3°C |\n| >0.030 | 4°C |\n\nThe configured maximum and target floor still apply. Change source mappings, rooms, model, curve and export discount under the integration's configuration options.")),
        section("Shared timing",text("Normal decisions: :00/:15/:30/:45. Advisor and method switches trigger immediate decisions. Protection switches update status immediately and affect the next decision. One-degree steps are separated by at least 25 minutes. Comfort requires a shortfall of at or above the configured threshold for 30 minutes and clears after 10 minutes below that level. Hydraulic hold lasts at most 30 minutes. Settings and source changes take effect at the next normal decision."))])
    return dict(title="Heat Pump Optimizer",views=[advice,price,efficiency,comfort,forecast,past,settings])

