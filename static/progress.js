const AutoXAKProgress = (() => {
    const revisions=new Map();
    async function refresh(id,days) {
        const root=document.getElementById(id), owner=AutoXAKSession.getUserId();
        if(!root || !owner) return;
        const revision=(revisions.get(id)||0)+1;revisions.set(id,revision);
        root.querySelectorAll('[data-days]').forEach(button=>button.setAttribute('aria-pressed',String(Number(button.dataset.days)===days)));
        try {
            const params=new URLSearchParams({days:String(days),utc_offset_minutes:String(-new Date().getTimezoneOffset())});
            const {response,data}=await AutoXAKSession.fetchJSON(`/api/v1/users/${encodeURIComponent(owner)}/progress?${params}`,{headers:AutoXAKSession.getAuthHeaders()});
            if(!response.ok || revisions.get(id)!==revision || AutoXAKSession.getUserId()!==owner) return;
            const values=root.querySelector('.progress-values'), empty=root.querySelector('.progress-empty'), chart=root.querySelector('.progress-chart');
            const available=Number.isFinite(data.average);
            values.style.display=available?'block':'none';
            empty.hidden=data.sufficient_data;
            empty.textContent=available?'Недостаточно данных для динамики. Совершите поездки в другие дни.':'Недостаточно данных. Совершите поездку, чтобы увидеть прогресс.';
            root.querySelector('.progress-number').textContent=available?String(data.average):'—';
            const delta=root.querySelector('.progress-delta');
            delta.textContent=Number.isFinite(data.delta)?`${data.delta>0?'+':''}${data.delta} пунктов`:'';
            delta.className='progress-delta'+(data.delta>0?' positive':data.delta<0?' negative':'');
            root.querySelector('.progress-caption').textContent=`Среднее за последние ${days} дней; сравнение с предыдущими ${days}`;
            if(chart) {
                chart.replaceChildren();chart.style.display=data.sufficient_data?'block':'none';
                if(!data.sufficient_data) return;
                const ns='http://www.w3.org/2000/svg';
                const add=(tag,attrs,text)=>{const e=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,v);if(text)e.textContent=text;chart.append(e);return e;};
                for(const score of [0,50,100]) {const y=120-score;add('line',{x1:28,x2:300,y1:y,y2:y,stroke:'#363A3F'});add('text',{x:0,y:y+4,fill:'#8C919B','font-size':10},String(score));}
                let segment=[];
                const flush=()=>{if(segment.length>1)add('polyline',{points:segment.join(' '),fill:'none',stroke:'#D2FA58','stroke-width':2});segment=[];};
                data.points.forEach((point,i)=>{
                    if(!Number.isFinite(point.score)){flush();return;}
                    const x=28+272*i/Math.max(1,data.points.length-1),y=120-point.score;
                    segment.push(`${x},${y}`);const dot=add('circle',{cx:x,cy:y,r:3,fill:'#D2FA58'});
                    const title=document.createElementNS(ns,'title');title.textContent=`${point.date}: ${point.score}`;dot.append(title);
                });flush();
                add('text',{x:28,y:143,fill:'#8C919B','font-size':10},data.period_start);
                add('text',{x:300,y:143,fill:'#8C919B','font-size':10,'text-anchor':'end'},data.period_end);
            }
        } catch(_) { /* Keep the last confirmed display during a network interruption. */ }
    }
    return {refresh};
})();
