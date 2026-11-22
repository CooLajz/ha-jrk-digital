// Shared by the yearly overview and the collection history.
const number = new Intl.NumberFormat("cs-CZ", { maximumFractionDigits: 2 });
const monthNames = Array.from({ length: 12 }, (_, i) =>
  new Intl.DateTimeFormat("cs-CZ", { month: "long" }).format(new Date(2020, i, 15))
);
const defaults = {
  view:"history", page_size:8, pagination:true, history_limit:0, days_back:0,
  show_header:true, show_place:true, show_updated:false, show_year:true, show_views:true, show_filters:true,
  show_time:true, show_bin:true, show_weight:true, show_cost:true, compact:true,
};
const numericSettings = {page_size:[1,100], history_limit:[0,10000], days_back:[0,366]};
const styles = `
:host{display:block;color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,inherit)}
ha-card{display:block;max-width:940px;margin:0 auto;overflow:hidden}
article{padding:clamp(16px,3vw,28px);container-type:inline-size}
header,.toolbar,footer,.tabs,.material{display:flex;align-items:center;gap:12px}
header,footer{justify-content:space-between}
h2,h3,p{margin:0}h2{font-size:1.45rem;letter-spacing:-.025em;font-weight:600}
h3{font-size:1rem;margin:22px 0 10px}.muted,small,label{color:var(--secondary-text-color)}
small{display:block;font-size:.79rem}header h2+small{margin-top:4px}
button,select{font:inherit;color:inherit;cursor:pointer}button{border:0;background:transparent}
button:focus-visible,select:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}
button:disabled{opacity:.35;cursor:default}
.tabs{gap:4px;margin:20px 0 16px;padding:4px;border-radius:12px;background:var(--secondary-background-color);width:fit-content}
.tabs button{font-size:.9rem;padding:8px 14px;border-radius:8px}
.tabs button[aria-pressed=true]{background:var(--card-background-color);color:var(--primary-text-color);box-shadow:0 1px 4px #0002;font-weight:600}
.toolbar{flex-wrap:wrap;margin:8px 0 16px}label{display:flex;align-items:center;gap:6px;font-size:.83rem}
select{border:1px solid var(--divider-color);padding:7px 8px;border-radius:8px;background:var(--card-background-color);color:var(--primary-text-color);max-width:100%}
table{width:100%;border-collapse:collapse;font-size:.92rem;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:10px 6px;border-bottom:1px solid var(--divider-color)}
th{font-size:.78rem;color:var(--secondary-text-color);font-weight:500}
th.num,td.num{text-align:right;white-space:nowrap}
.history{white-space:nowrap}
.history-scroll{max-width:100%;overflow-x:auto}
.compact .history :is(th,td){padding:4px 5px;line-height:1.35}
.clock{color:var(--secondary-text-color)}
article>header:has(+.history-scroll),article>header:has(+.toolbar){margin-bottom:12px}
@container(max-width:460px){.compact .history :is(th,td){padding:4px 2px}}
.dot{width:9px;height:9px;border-radius:50%;background:var(--secondary-text-color);flex:none}
.dot[data-kind="Plasty"]{background:#e5ae36}.dot[data-kind="Papír"]{background:#4b9ad4}.dot[data-kind="SKO"]{background:#87938e}
.material{gap:8px}.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:16px 0}
.kpi{border:1px solid var(--divider-color);border-radius:12px;padding:12px}.kpi strong{display:block;font-size:1.27rem;margin-top:6px}
footer{margin-top:14px}footer button,.month{color:var(--primary-color);padding:5px}.month{text-align:left;text-decoration:underline;text-underline-offset:3px}
.updated{margin-top:12px}.empty{padding:16px 0;color:var(--secondary-text-color)}
@container(max-width:460px){.kpis{gap:5px}.kpi{padding:8px}.kpi strong{font-size:1.08rem}th,td{padding:9px 2px}table{font-size:.83rem}.material{gap:5px}.toolbar{gap:6px}footer{flex-wrap:wrap}}
`;

function element(tag, text, className) {
  const el = document.createElement(tag);
  if (text != null) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function button(label, onClick, className) {
  const b = element("button", label, className);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}
function selectField(label, options, value, onChange) {
  const wrapper = element("label", label);
  const select = element("select");
  for (const [key, text] of options) select.add(new Option(text, key));
  select.value = value;
  select.onchange = () => onChange(select.value);
  wrapper.append(select);
  return wrapper;
}
function table(headers) {
  const t = element("table");
  const header = t.createTHead().insertRow();
  for (const [name, numeric] of headers) header.append(element("th", name, numeric ? "num" : ""));
  t.createTBody();
  return t;
}
function cell(row, value, numeric = false) {
  const td = row.insertCell();
  if (numeric) td.className = "num";
  if (value instanceof Node) td.append(value);
  else td.textContent = value;
  return td;
}
function materialLabel(name) {
  const wrap = element("span", null, "material");
  const dot = element("span", null, "dot");
  dot.dataset.kind = name;
  wrap.append(dot, document.createTextNode(name || "Neurčeno"));
  return wrap;
}
function measurement(value, unit) {
  return value == null ? "—" : (number.format(value) + " " + unit).trim();
}
function shortDate(iso) {
  const [y,m,d] = iso.slice(0,10).split("-");
  return d + "." + m + "." + y;
}

class JRKWasteCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode:"open"});
    const style = element("style", styles);
    const card = element("ha-card");
    this.body = element("article");
    card.append(this.body);
    this.shadowRoot.append(style,card);
    this.serial = 0;
    this.month = "";
    this.material = "";
    this.page = 0;
  }
  static getConfigElement() { return document.createElement("jrk-waste-editor"); }
  static getStubConfig(hass) {
    const id = Object.keys(hass.states).find(k=>k.startsWith("calendar.") && k.includes("historie_svozu"));
    return {entity:id || "",view:"history"};
  }
  setConfig(config) {
    if (!config.entity || !config.entity.startsWith("calendar.")) throw new Error("Vyberte kalendář historie svozů JRK.");
    this.config = {...defaults, ...config};
    for (const [key,[min,max]] of Object.entries(numericSettings)) {
      const value = this.config[key];
      if (!Number.isInteger(value) || value<min || value>max) throw new Error("Hodnota " + key + " musí být celé číslo od " + min + " do " + max + ".");
    }
    if (config.materials != null && !Array.isArray(config.materials)) throw new Error("Vyberte druhy odpadu ze seznamu.");
    this.body.classList.toggle("compact",this.config.compact);
    this.view = config.view === "year" ? "year" : "history";
    this.year = undefined;
    this.month = this.material = "";
    this.page = 0;
    this.requestKey = null;
    this.data = null;
    this.load();
  }
  set hass(hass) { this._hass = hass; this.load(); }
  connectedCallback() { this.requestKey = null; this.load(); }
  disconnectedCallback() { this.serial++; }
  getCardSize() { return this.view === "year" ? 12 : 10; }
  getGridOptions() { return {columns:"full", rows:"auto"}; }

  async load() {
    if (!this.isConnected || !this._hass || !this.config) return;
    const state = this._hass.states[this.config.entity];
    const signature = [this.config.entity, this.year || "", state?.state,
      state?.attributes.revize_synchronizace, state?.attributes.ulozena_data, state?.attributes.datum_synchronizace,
      new Date().toLocaleDateString("sv-SE")].join("|");
    if (signature === this.requestKey) return;
    this.requestKey = signature;
    const serial = ++this.serial;
    if (!this.data) this.body.textContent = "Načítání přehledu…";
    try {
      const request = {type:"jrk_digital/report",entity_id:this.config.entity};
      if (this.year) request.year = this.year;
      const data = await this._hass.callWS(request);
      if (serial !== this.serial) return;
      this.data = data;
      this.render();
    } catch (e) {
      if (serial !== this.serial) return;
      this.data = null;
      const msg = element("p",e.message || "Přehled se nepodařilo načíst.","empty");
      this.body.replaceChildren(msg,button("Zkusit znovu",()=>{this.requestKey=null;this.load();}));
    }
  }

  get selectedRows() {
    const materials=this.config.materials;
    return materials?.length ? this.data.rows.filter(r=>materials.includes(r.material)) : this.data.rows;
  }

  render() {
    const d = this.data;
    if (!d) return;
    const header = element("header"), title = element("div");
    if (this.config.show_header) title.append(element("h2",this.config.title || "Přehled odpadů"));
    if (this.config.show_place && d.place) title.append(element("small",d.place));
    if (title.childElementCount) header.append(title);
    if (this.config.show_year) header.append(selectField("Rok",d.years.map(y=>[String(y),String(y)]),String(d.year),v=>{
      this.year=Number(v);this.month=this.material="";this.page=0;this.data=null;this.load();
    }));
    const tabs = element("div",null,"tabs");
    for (const [key,name] of [["history","Historie svozů"],["year","Roční přehled"]]) {
      const b = button(name,()=>{this.view=key;this.render();});
      b.setAttribute("aria-pressed",String(this.view===key)); tabs.append(b);
    }
    this.body.replaceChildren();
    if (header.childElementCount) this.body.append(header);
    if (this.config.show_views) this.body.append(tabs);
    if (this.view==="year") this.renderYear(); else this.renderHistory();
    if (this.config.show_updated || d.cached) {
      const label = d.cached ? "Uložená data ze dne " : "Data aktualizována ";
      this.body.append(element("small",label + shortDate(d.updated),"updated"));
    }
  }

  renderHistory() {
    const d=this.data;
    const options = [["","Celý rok"],...monthNames.map((m,i)=>[String(i+1),m])];
    const source=this.selectedRows;
    const materials = [...new Set(source.map(r=>r.material))].sort((a,b)=>a.localeCompare(b,"cs"));
    const bar=element("div",null,"toolbar");
    bar.append(selectField("Měsíc",options,this.month,v=>{this.month=v;this.page=0;this.render();}),
      selectField("Druh",[["","Všechny druhy"],...materials.map(m=>[m,m])],this.material,v=>{this.material=v;this.page=0;this.render();}));
    if (this.config.show_filters) this.body.append(bar);
    const today = new Intl.DateTimeFormat("sv-SE",{timeZone:"Europe/Prague"}).format(new Date());
    const lastDay = today.slice(0,4)===String(d.year) ? today : d.year+"-12-31";
    const cutoff = new Date(lastDay+"T00:00:00Z");
    cutoff.setUTCDate(cutoff.getUTCDate()-this.config.days_back+1);
    const firstDay=this.config.days_back ? cutoff.toISOString().slice(0,10) : "";
    let rows=source.filter(r=>r.date.slice(0,10)>=firstDay && (!this.month || Number(r.date.slice(5,7))===Number(this.month)) && (!this.material || r.material===this.material));
    if (!rows.length) {this.body.append(element("p","Za vybrané období není evidován žádný odpovídající svoz.","empty"));return;}
    if (this.config.history_limit) rows=rows.slice(0,this.config.history_limit);
    const pageSize=this.config.page_size;
    const headers=[["Datum"],["Odpad"]];
    if(this.config.show_bin) headers.push(["Objem",true]);
    if(this.config.show_weight) headers.push(["Hmotnost",true]);
    if(this.config.show_cost) headers.push(["Cena",true]);
    const t=table(headers);
    t.className="history";
    this.page=Math.min(this.page,Math.floor((rows.length-1)/pageSize));
    const offset=this.page*pageSize;
    for (const r of rows.slice(offset,offset+pageSize)) {
      const tr=t.tBodies[0].insertRow();
      const stamp=element("time",shortDate(r.date));
      stamp.dateTime=r.date;
      if(this.config.show_time) stamp.append(" ",element("span",r.date.slice(11,16),"clock"));
      stamp.title=shortDate(r.date)+" "+r.date.slice(11,16)+" (Europe/Prague)";
      cell(tr,stamp);
      const name=materialLabel(r.material);
      if (r.bin) name.title=r.bin;
      cell(tr,name);
      if(this.config.show_bin) cell(tr,measurement(r.volume,"l"),true);
      if(this.config.show_weight) cell(tr,measurement(r.kg,"kg"),true);
      if(this.config.show_cost) {
        const cost=cell(tr,measurement(r.cost,d.currency==="CZK"?"Kč":d.currency || ""),true);
        if (r.cost==null) cost.title="JRK u tohoto svozu neuvádí cenu.";
      }
    }
    const foot=element("footer");
    foot.append(element("small",`${offset+1}–${Math.min(offset+pageSize,rows.length)} z ${rows.length} svozů`));
    if (rows.length>pageSize) {
      const nav=element("div");
      const back=button("Předchozí",()=>{this.page--;this.render();});
      const next=button("Další",()=>{this.page++;this.render();});
      back.disabled=this.page===0; next.disabled=offset+pageSize>=rows.length;
      nav.append(back,next);foot.append(nav);
    }
    const scroll=element("div",null,"history-scroll");
    scroll.append(t);this.body.append(scroll);
    if(this.config.pagination) this.body.append(foot);
  }

  renderYear() {
    const d=this.data;
    const currency=d.currency==="CZK"?"Kč":d.currency || "";
    const rows=this.selectedRows;
    const restricted=Boolean(this.config.materials?.length);
    const reports=restricted ? {} : d.months;
    if(restricted) {
      for (const row of rows) {
        const key=row.date.slice(0,7);
        const m=reports[key] ??= {cost:null};
        if(row.cost!=null) m.cost=(m.cost??0)+row.cost;
      }
    }
    const months=Object.entries(reports).sort(([a],[b])=>a.localeCompare(b));
    const cost=months.some(([,m])=>m.cost!=null) ? months.reduce((total,[,m])=>total+(m.cost??0),0) : null;
    const kpis=element("div",null,"kpis");
    const metrics=[["Provedené svozy",rows.length]];
    if(this.config.show_cost) {
      metrics.push(["Cena svozů",measurement(cost,currency)]);
      if(!restricted) metrics.push(["Poplatek JRK",measurement(months.at(-1)?.[1].running,currency)]);
    }
    for (const [label,value] of metrics) {
      const box=element("div",null,"kpi");box.append(element("small",label),element("strong",String(value)));kpis.append(box);
    }
    this.body.append(kpis);
    const types=new Map(),byMonth=new Map();
    for (const r of rows) {
      const group=types.get(r.material) || {count:0,kg:0,known:0};
      group.count++;if (r.kg!=null){group.kg+=r.kg;group.known++;}types.set(r.material,group);
      const month=r.date.slice(0,7);byMonth.set(month,(byMonth.get(month)||0)+1);
    }
    const groupHeaders=[["Druh odpadu"],["Svozy",true]];
    if(this.config.show_weight) groupHeaders.push(["Hmotnost",true]);
    const t=table(groupHeaders);
    for(const [name,g] of [...types].sort(([a],[b])=>a.localeCompare(b,"cs"))) {
      const tr=t.tBodies[0].insertRow();cell(tr,materialLabel(name));cell(tr,g.count,true);
      if(this.config.show_weight) cell(tr,g.known===g.count?measurement(g.kg,"kg"):"—",true);
    }
    if(types.size) this.body.append(element("h3","Podle druhu odpadu"),t);
    const feeHeaders=[["Měsíc"],["Svozy",true]];
    if(this.config.show_cost) {
      feeHeaders.push(["Cena svozů",true]);
      if(!restricted) feeHeaders.push(["Poplatek od ledna",true]);
    }
    const fees=table(feeHeaders);
    for(const key of [...new Set([...byMonth.keys(),...Object.keys(reports)])].sort().reverse()) {
      const tr=fees.tBodies[0].insertRow(),month=Number(key.slice(5,7)),name=monthNames[month-1];
      cell(tr,this.config.show_views ? button(name.charAt(0).toUpperCase()+name.slice(1),()=>{
        this.month=String(month);this.material="";this.page=0;this.view="history";this.render();
      },"month") : name);
      cell(tr,byMonth.get(key)||0,true);
      if(this.config.show_cost) cell(tr,measurement(reports[key]?.cost,currency),true);
      if(this.config.show_cost && !restricted) cell(tr,measurement(reports[key]?.running,currency),true);
    }
    if(fees.tBodies[0].rows.length)this.body.append(element("h3","Podle měsíce"),fees);
    else this.body.append(element("p","Za vybraný rok nejsou evidovány žádné svozy ani poplatky.","empty"));
  }
}

const settingLabels = {
  entity:"Svozové místo",title:"Nadpis",view:"Výchozí pohled",materials:"Zobrazené druhy odpadu",
  page_size:"Počet řádků na stránku",pagination:"Zobrazit stránkování",history_limit:"Nejvýše záznamů po filtrování",
  days_back:"Počet posledních dní ve vybraném roce",show_header:"Zobrazit nadpis",show_place:"Zobrazit svozové místo",show_updated:"Datum aktualizace",
  show_year:"Výběr roku",show_views:"Přepínač historie a ročního přehledu",show_filters:"Filtry měsíce a druhu",
  show_time:"Čas svozu",show_bin:"Objem (l)",show_weight:"Hmotnost",show_cost:"Cena",compact:"Kompaktní řádky"
};

class JRKWasteEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode:"open"});
    this.form=element("ha-form");
    this.message=element("p");
    this.form.computeLabel=s=>settingLabels[s.name] || s.name;
    this.form.computeHelper=s=>({
      materials:"Prázdný výběr zahrne všechny druhy tohoto místa.",
      history_limit:"0 zobrazí všechny odpovídající záznamy.",
      days_back:"0 zahrne celý rok. U letošního roku se období počítá ode dneška.",
      pagination:"Po vypnutí se zobrazí pouze první stránka.",
    })[s.name];
    this.form.addEventListener("value-changed",e=>{
      e.stopPropagation();
      const next={...this.config,...e.detail.value};
      if(next.entity!==this.config.entity) delete next.materials;
      for(const [key,[min,max]] of Object.entries(numericSettings)) {
        const value=Number(next[key] ?? defaults[key]);
        if(!Number.isInteger(value)||value<min||value>max)return;
        next[key]=value;
      }
      this.setConfig(next);
      this.dispatchEvent(new CustomEvent("config-changed",{detail:{config:next},bubbles:true,composed:true}));
    });
    this.shadowRoot.append(this.message,this.form);
  }
  setConfig(config){this.config=config;this.update();}
  set hass(hass){this._hass=hass;this.form.hass=hass;this.update();}
  connectedCallback(){this.update();}
  async update(){
    if(!this._hass || !this.config || !this.isConnected)return;
    const entity=this.config.entity || "";
    if(this.entity!==entity || this.connection!==this._hass.connection){
      this.entity=entity; this.connection=this._hass.connection;
      const serial=this.serial=(this.serial||0)+1;
      this.materials=[];this.message.textContent="";
      try {
        const [places,report]=await Promise.all([this._hass.callWS({type:"jrk_digital/places"}),
          entity?this._hass.callWS({type:"jrk_digital/report",entity_id:entity}).catch(()=>null):null]);
        if(serial!==this.serial)return;
        this.places=places;this.materials=report?.materials||[];
        if(entity&&!report)this.message.textContent="Vyberte dostupné svozové místo.";
        this.schema=null;
      }catch{
        if(serial!==this.serial)return;
        this.message.textContent="Seznam míst se nepodařilo načíst. Otevřete editor znovu.";
      }
    }
    if(!this.schema || this.schemaEntity!==entity){
      const select=(name,options,multiple=false)=>({name,selector:{select:{options,multiple,mode:"dropdown"}}});
      const check=name=>({name,selector:{boolean:{}}});
      const numeric=name=>({name,selector:{number:{min:numericSettings[name][0],max:numericSettings[name][1],step:1,mode:"box"}}});
      const group=(name,title,schema)=>({type:"expandable",name:"",title,schema,icon:name});
      this.schemaEntity=entity;
      this.schema=[select("entity",this.places||[]),{name:"title",selector:{text:{}}},
        select("view",[{value:"history",label:"Historie svozů"},{value:"year",label:"Roční přehled"}]),
        select("materials",this.materials||[],true),
        group("mdi:format-list-numbered","Rozsah historie a stránkování",[
          numeric("page_size"),check("pagination"),numeric("history_limit"),numeric("days_back")]),
        group("mdi:table","Tabulka a sloupce",["compact","show_time","show_bin","show_weight","show_cost"].map(check)),
        group("mdi:tune","Ovládání karty",["show_header","show_place","show_updated","show_year","show_views","show_filters"].map(check))];
      this.form.schema=this.schema;
    }
    if(this.formConfig!==this.config){
      this.formConfig=this.config;
      this.form.data={...defaults,title:"Přehled odpadů",materials:[],...this.config};
    }
  }
}

if (!customElements.get("jrk-waste-editor")) customElements.define("jrk-waste-editor",JRKWasteEditor);

if (!customElements.get("jrk-waste-card")) {
  customElements.define("jrk-waste-card",JRKWasteCard);
  window.customCards = window.customCards || [];
  window.customCards.push({type:"jrk-waste-card",name:"JRK Digital · Přehled odpadů",
    description:"Prohlédněte si historii svozů a roční přehled vybraného svozového místa."});
}
