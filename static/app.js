const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "—").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));
const pill = (text, kind="") => `<span class="pill ${kind}">${esc(text)}</span>`;
let registration;

function urlBase64ToBytes(value){const padding="=".repeat((4-value.length%4)%4);const raw=atob((value+padding).replace(/-/g,"+").replace(/_/g,"/"));return Uint8Array.from(raw,c=>c.charCodeAt(0));}
function setPushButton(enabled){const button=$("push-toggle");button.classList.toggle("on",enabled);button.setAttribute("aria-checked",String(enabled));button.lastChild.textContent=enabled?"Push an":"Push";}
async function syncSubscription(subscription,remove=false){await fetch("/api/push-subscriptions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(remove?{endpoint:subscription.endpoint,remove:true}:subscription)});}
async function setupPush(){
  if(!("serviceWorker" in navigator)||!("PushManager" in window)||!("Notification" in window)){ $("push-toggle").hidden=true; return; }
  registration=await navigator.serviceWorker.ready;
  setPushButton(Boolean(await registration.pushManager.getSubscription()));
  $("push-toggle").addEventListener("click",async()=>{
    try{
      const old=await registration.pushManager.getSubscription();
      if(old){await syncSubscription(old,true);await old.unsubscribe();setPushButton(false);return;}
      if(await Notification.requestPermission()!=="granted"){setPushButton(false);return;}
      const config=await (await fetch("/api/push-config")).json();
      const subscription=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:urlBase64ToBytes(config.publicKey)});
      await syncSubscription(subscription);setPushButton(true);
    }catch(error){console.warn("Push konnte nicht aktiviert werden",error);setPushButton(false);}
  });
}

function render(data){
  const jobs=(data.blender_jobs||[]).map(j=>{
    const progress=j.expected_frames?`${j.frame??0} / ${j.expected_frames} Frames`:"Startet …";
    const state=j.paused?"pausiert":j.running?"läuft":"angehalten";
    return `<article class="card"><div class="row"><strong>${esc(j.machine)} · ${esc(j.label)}</strong>${pill(state,j.running?"ok":"wait")}</div><div class="value">${esc(progress)}</div></article>`;
  });
  $("jobs").innerHTML=jobs.length?jobs.join(""):`<article class="card"><strong>Kein aktiver Blender-Job</strong></article>`;
  $("updated").textContent=new Date(data.updated_at*1000).toLocaleTimeString("de-AT",{hour:"2-digit",minute:"2-digit",second:"2-digit"});$("refresh").textContent="Live";
}
async function refresh(initial=false){try{const r=await fetch("/api/status",{cache:"no-store"});render(await r.json());if(initial)$("loading").classList.add("done")}catch(e){$("refresh").textContent="Verbindung fehlt";if(initial)$("loading").classList.add("done")}}
if("serviceWorker" in navigator){navigator.serviceWorker.register("/sw.js").then(setupPush).catch(()=>{});}refresh(true);setInterval(refresh,15000);
