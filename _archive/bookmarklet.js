/**
 * ============================================================
 * GTO分析ブックマークレット
 * TenFour のブックマークページを開いた状態でタップ/クリックすると
 * ハンドを自動収集 → サーバーに送信 → 進捗ページが別タブで開く → PDF完成
 * ============================================================
 *
 * ■ 登録方法 — PC Chrome / Edge
 *   1. ブックマークバーを右クリック →「ページを追加」(または新しいブックマーク)
 *   2. 名前: 「GTO分析」など（自由）
 *   3. URL: 下の「=== 1行版 ===」の内容をそのままコピーして貼る
 *   4. 保存してブックマークバーをクリックすれば起動
 *
 * ■ 登録方法 — iPhone Safari
 *   1. Safari でどこかのページをブックマーク保存
 *      （画面下の共有ボタン → 「ブックマークを追加」）
 *   2. ブックマーク一覧を開く → 保存したものを左スワイプ or 長押し →「編集」
 *   3. URLフィールドを全選択して削除し、下の1行版をペーストして保存
 *   4. TenFour を開き、アドレスバーに「GTO」と入力
 *      → 候補にブックマークが出るのでタップ（= 実行される）
 *
 * ■ 登録方法 — Android Chrome
 *   1. Chrome でどこかのページを☆保存（⋮メニュー →「ブックマークに追加」）
 *   2. ⋮メニュー →「ブックマーク」→ 保存したものを長押し →「編集」
 *   3. URLフィールドを下の1行版に書き換えて保存
 *   4. TenFour を開き、アドレスバーに「GTO」と入力 → 候補をタップ
 *
 * ============================================================
 * === 1行版（ここをコピーしてブラウザのURL欄に貼る）===
 * ============================================================
 */

// ↓↓↓ この1行をまるごとコピーしてブックマークのURLに貼る ↓↓↓
javascript:(async function(){const SERVER_URL='https://gto-production.up.railway.app';const sleep=ms=>new Promise(r=>setTimeout(r,ms));const cards=[...document.querySelectorAll('*')].filter(e=>/2026|2025/.test(e.textContent)&&/bb/.test(e.textContent)&&e.children.length>0&&e.children.length<8&&e.offsetHeight>30&&e.offsetHeight<150);if(!cards.length){alert('ハンドが見つかりません。ブックマークタブを開いているか確認してください。');return;}const results=[];console.log(cards.length+'件処理開始...');for(let i=0;i<cards.length;i++){cards[i].scrollIntoView({block:'center'});await sleep(200);cards[i].click();await sleep(700);const overlay=[...document.querySelectorAll('*')].find(e=>window.getComputedStyle(e).position==='fixed'&&e.innerText&&e.innerText.includes('ハンドヒストリー詳細'));if(overlay){results.push('='.repeat(60)+'\nハンド '+(i+1)+' / '+cards.length+'\n'+'='.repeat(60)+'\n'+overlay.innerText.trim()+'\n');const btn=overlay.querySelector('button[title="ブックマーク解除"]');if(btn){btn.click();await sleep(300);}}else{results.push('=== ハンド '+(i+1)+' (取得失敗) ===\n'+cards[i].textContent.trim()+'\n');}document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',keyCode:27,bubbles:true}));await sleep(400);}if(!results.length){alert('取得できたハンドが0件でした。');return;}try{const res=await fetch(SERVER_URL+'/scrape_upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:results.join('\n')})});if(!res.ok){const e=await res.json().catch(function(){return{};});alert('送信エラー: '+(e.error||res.status));return;}const data=await res.json();window.open(SERVER_URL+'/progress/'+data.job_id,'_blank');}catch(e){alert('通信エラー: '+e.message);}})();
// ↑↑↑ ここまで ↑↑↑


/**
 * ============================================================
 * 可読版（参考・編集用）
 * ============================================================
 */
/*
(async function () {
  const SERVER_URL = 'https://gto-production.up.railway.app';
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // ハンドカード要素を収集
  const cards = [...document.querySelectorAll('*')].filter(e =>
    /2026|2025/.test(e.textContent) &&
    /bb/.test(e.textContent) &&
    e.children.length > 0 &&
    e.children.length < 8 &&
    e.offsetHeight > 30 &&
    e.offsetHeight < 150
  );

  if (!cards.length) {
    alert('ハンドが見つかりません。ブックマークタブを開いているか確認してください。');
    return;
  }

  const results = [];
  console.log(cards.length + '件処理開始...');

  for (let i = 0; i < cards.length; i++) {
    cards[i].scrollIntoView({ block: 'center' });
    await sleep(200);
    cards[i].click();
    await sleep(700);

    const overlay = [...document.querySelectorAll('*')].find(e =>
      window.getComputedStyle(e).position === 'fixed' &&
      e.innerText && e.innerText.includes('ハンドヒストリー詳細')
    );

    if (overlay) {
      results.push(
        '='.repeat(60) + '\n' +
        'ハンド ' + (i + 1) + ' / ' + cards.length + '\n' +
        '='.repeat(60) + '\n' +
        overlay.innerText.trim() + '\n'
      );
      // ブックマーク解除
      const btn = overlay.querySelector('button[title="ブックマーク解除"]');
      if (btn) { btn.click(); await sleep(300); }
    } else {
      results.push('=== ハンド ' + (i + 1) + ' (取得失敗) ===\n' + cards[i].textContent.trim() + '\n');
    }

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', keyCode: 27, bubbles: true }));
    await sleep(400);
  }

  if (!results.length) {
    alert('取得できたハンドが0件でした。');
    return;
  }

  // サーバーに送信
  try {
    const res = await fetch(SERVER_URL + '/scrape_upload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: results.join('\n') }),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      alert('送信エラー: ' + (e.error || res.status));
      return;
    }
    const data = await res.json();
    // 進捗ページを別タブで開く
    window.open(SERVER_URL + '/progress/' + data.job_id, '_blank');
  } catch (e) {
    alert('通信エラー: ' + e.message);
  }
})();
*/
