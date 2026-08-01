import { decodeDownlinkHex } from './loraCodec'

// ==========================================================
// ★ 実行環境の自動判別 ★
// ==========================================================
// isAndroidフラグで、Androidアプリ内で実行されているかを判断
const isAndroid = typeof window.androidSerial !== 'undefined';


// ==========================================================
// ★ 従来のWeb Serial API用変数 (PCブラウザ用) ★
// ==========================================================
let port = null;
let reader = null;
let writer = null;
let readableStreamClosed = null;


// ==========================================================
// ★ 内部状態フラグ (共通) ★
// ==========================================================
let _isPortConnected = false;
let _isNetworkJoined = false;
let isInitializing = false;
let isJoinCommandActive = false;
let joinPromise = {};


// ==========================================================
// ★ コールバック関数 (共通) ★
// ==========================================================
let onDataReceived = () => {};
let onDisconnected = () => {};


// ==========================================================
// ★ 受信データ処理 (共通ロジック) ★
// ==========================================================
// Web Serial APIとAndroidブリッジの両方から呼び出される共通の処理関数
async function processLine(fullResponse) {
  document.dispatchEvent(new CustomEvent('lora-line-received', { detail: fullResponse }));
  console.log(`[LoRa MSG] 受信:`, fullResponse);

  if (isJoinCommandActive) {
    if (fullResponse.includes('JOINED') || fullResponse.includes('+JOIN: OK')) {
      console.log("LoRaWAN参加成功！");
      _isNetworkJoined = true;
      isJoinCommandActive = false;
      if (joinPromise.resolve) joinPromise.resolve(true);
    } else if (fullResponse.includes('+JOIN: Failed')) {
      console.error("LoRaWAN参加失敗");
      _isNetworkJoined = false;
      isJoinCommandActive = false;
      if (joinPromise.reject) joinPromise.reject(new Error('LoRaWAN Join Failed'));
    } else if (fullResponse.includes('AT_ERROR') && Date.now() - joinPromise.startTime > 3000) {
      console.error("LoRaWAN参加中にエラーを検知");
      _isNetworkJoined = false;
      isJoinCommandActive = false;
      if (joinPromise.reject) joinPromise.reject(new Error('LoRaWAN Join Error'));
    }
  }

  // "rxDone" を受信したら、データを取得するコマンドを送信
  if (fullResponse.includes('rxDone')) {
    console.log('[LoRa] データ受信完了。ペイロードを取得します...');
    // ATコマンドは `_write` ヘルパー経由で送信する
    await _write('AT+RECVB=?\r\n');
  }

  // 正規表現を使って "<port>:<hex>" の形式を直接マッチさせる
  const match = fullResponse.match(/^(\d+):([0-9a-fA-F]+)$/);
  if (match) {
    try {
      const fport = parseInt(match[1], 10);
      const hexData = match[2];
      console.log(`[LoRa DATA] Port ${fport} で受信したペイロードHEX: ${hexData}`);
      const downlink = decodeDownlinkHex(hexData);
      if (!downlink) return;
      onDataReceived(downlink);
    } catch (e) { console.error('受信データのパースに失敗:', e); }
  }
}


// ==========================================================
// ★ Androidブリッジ用のコールバック設定 ★
// ==========================================================
if (isAndroid) {
  let lineBuffer = '';

  window.onSerialConnect = () => {
    console.log('[LoRa Native] ポートに接続しました。');
    _isPortConnected = true;
  };
  
  window.onSerialDisconnect = () => {
    console.log('[LoRa Native] ポートから切断されました。');
    _isPortConnected = false;
    _isNetworkJoined = false;
    if(onDisconnected) onDisconnected();
  };
  
  window.onSerialDataReceived = (receivedString) => {
    lineBuffer += receivedString;
    let lines = lineBuffer.split(/\r?\n/);
    lineBuffer = lines.pop();
  
    for (const line of lines) {
      const fullResponse = line.trim();
      if (fullResponse === '') continue;
      processLine(fullResponse); // 共通の処理関数を呼び出す
    }
  };
}


// ==========================================================
// ★ Web Serial API用の読み取りループ ★
// ==========================================================
async function readLoop() {
  if (!reader) return;
  console.log('[LoRa] 読み取りループを開始します。');
  let lineBuffer = '';

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      
      lineBuffer += value;
      let lines = lineBuffer.split(/\r?\n/);
      lineBuffer = lines.pop();

      for (const line of lines) {
        const fullResponse = line.trim();
        if (fullResponse === '') continue;
        processLine(fullResponse); // 共通の処理関数を呼び出す
      }
    }
  } catch (error) {
    if (error.name !== 'AbortError') console.error('読み取りループでエラー:', error);
  }
  console.log('[LoRa] 読み取りループが停止しました。');
}


// ==========================================================
// ★ 公開API: 接続処理 (connect) ★
// ==========================================================
export async function connect(onData, onDisconnectCb) {
  if (_isPortConnected) return true;

  onDataReceived = onData;
  onDisconnected = onDisconnectCb;

  // --- Androidの場合 ---
  if (isAndroid) {
    console.log('ネイティブブリッジ経由での接続を待ちます...');
    _isPortConnected = true; // ネイティブ側で自動接続される前提
    return true;
  }
  
  // --- PCブラウザの場合 ---
  if (navigator.serial) {
    isInitializing = true;
    try {
      port = await navigator.serial.requestPort();
      await port.open({ baudRate: 9600 });

      writer = port.writable.getWriter();
      
      const textDecoder = new TextDecoderStream();
      readableStreamClosed = port.readable.pipeTo(textDecoder.writable);
      reader = textDecoder.readable.getReader();
      
      _isPortConnected = true;
      _isNetworkJoined = false;
      
      readLoop();

      console.log('シリアルポートに接続しました。');
      isInitializing = false;
      return true;
    } catch (error) {
      console.error('シリアルポート接続失敗:', error);
      isInitializing = false;
      return false;
    }
  }

  // --- どちらの環境でもない場合 ---
  alert('Web Serial API非対応ブラウザです。');
  return false;
}


// ==========================================================
// ★ 公開API: 切断処理 (disconnect) ★
// ==========================================================
export async function disconnect() {
  // --- Androidの場合 ---
  if (isAndroid) {
    console.log('切断処理はアプリのライフサイクルに依存します。');
    // JS側から能動的に切断はしない
  } 
  // --- PCブラウザの場合 ---
  else {
    if (reader) {
      try {
        await reader.cancel();
        if (readableStreamClosed) await readableStreamClosed.catch(() => {});
      } catch(e) { /* ignore errors */ }
      reader = null;
    }
    if (writer) {
      try {
        writer.releaseLock();
      } catch(e) { /* ignore errors */ }
      writer = null;
    }
    if (port) {
      try {
        await port.close();
      } catch (e) { /* ignore errors */ }
      port = null;
    }
    console.log('シリアルポートを切断しました。');
  }

  _isPortConnected = false;
  _isNetworkJoined = false;
  isInitializing = false;
}


// ==========================================================
// ★ データ送信ヘルパー関数 (_write) ★
// ==========================================================
// 送信処理を共通化
async function _write(dataString) {
  // --- Androidの場合 ---
  if (isAndroid) {
    if (window.androidSerial?.send) {
      window.androidSerial.send(dataString);
    }
    return;
  }
  // --- PCブラウザの場合 ---
  if (writer) {
    const encoder = new TextEncoder();
    await writer.write(encoder.encode(dataString));
  }
}


// ==========================================================
// ★ 公開API: LoRaWAN参加 (join) ★
// ==========================================================
export function join() {
  if (!_isPortConnected) return Promise.reject(new Error('ポート未接続'));
  if (isJoinCommandActive) return Promise.reject(new Error('Join処理実行中'));

  return new Promise((resolve, reject) => {
    isJoinCommandActive = true;
    joinPromise = { resolve, reject, startTime: Date.now() };

    const writeCmd = async (cmd) => {
      console.log(`コマンド送信: ${cmd}`);
      await _write(cmd + '\r\n'); // 送信ヘルパーを利用
      await new Promise(r => setTimeout(r, 500));
    };

    // joinシーケンスは元のロジックをそのまま使用
    const startJoinSequence = async () => {
      try {
        console.log('ネットワーク参加状態を確認します... (AT+NJS=?)');
        
        const njsPromise = new Promise((res, rej) => {
            const checkNJSHandler = (event) => {
                const line = event.detail;
                if (typeof line !== 'string') return;
                const trimmedLine = line.trim();
                if (trimmedLine === '1' || trimmedLine === '0') {
                    document.removeEventListener('lora-line-received', checkNJSHandler);
                    res(trimmedLine === '1');
                }
            };
            document.addEventListener('lora-line-received', checkNJSHandler);
            setTimeout(() => {
              document.removeEventListener('lora-line-received', checkNJSHandler);
              rej(new Error('AT+NJS=? response timeout'));
            }, 5000);
        });

        await writeCmd('AT+NJS=?');
        const isJoined = await njsPromise;
        
        if (isJoined) {
          console.log('既にネットワークに参加済みです。Join処理をスキップします。');
          _isNetworkJoined = true;
          isJoinCommandActive = false;
          resolve(true);
          return;
        }

        console.log('ネットワークに未参加です。設定とJoinを開始します。');
        await writeCmd('AT+MODE=LWOTAA');
        await writeCmd('AT+DEUI=A8 40 41 68 E1 89 62 1F');
        await writeCmd('AT+APPEUI=A840410000000101');
        await writeCmd('AT+APPKEY=138F1A42A61329A3918FC834BCAD0071');
        await writeCmd('AT+JOIN');
        
      } catch (e) {
        isJoinCommandActive = false;
        reject(e);
      }
    };
    startJoinSequence();

    const timeoutId = setTimeout(() => {
      if (isJoinCommandActive) {
        isJoinCommandActive = false;
        reject(new Error('Join command timed out'));
      }
    }, 30000);

    joinPromise.resolve = (val) => { clearTimeout(timeoutId); resolve(val); };
    joinPromise.reject = (err) => { clearTimeout(timeoutId); reject(err); };

  }).finally(() => {
    isInitializing = false;
  });
}


// ==========================================================
// ★ 公開API: 状態取得 (getIsJoined) ★
// ==========================================================
export function getIsJoined() {
  return _isPortConnected && _isNetworkJoined;
}


// ==========================================================
// ★ 公開API: データ送信 (send) ★
// ==========================================================
export async function send(data) {
  // isAndroidの場合はネイティブの `send` がATコマンドを直接受け取るため、
  // この `send` 関数は主にアップリンクペイロードのために使われる。
  // JoinなどのATコマンドは `_write` を直接呼び出すことで送信される。
  if (!_isNetworkJoined) {
    console.warn('LoRa未接続のため送信をスキップしました。');
    return;
  }
  
  const encoder = new TextEncoder();
  const dataBytes = encoder.encode(data);
  const hexData = Array.from(dataBytes)
      .map(b => b.toString(16).padStart(2, '0')).join('');
  const dataLen = dataBytes.length;

  const confirmStatus = 0;
  const fport = 2;
  const commandString = `AT+SENDB=${confirmStatus},${fport},${dataLen},${hexData}\r\n`;

  console.log(`データ送信: ${JSON.stringify(commandString)}`);
  await _write(commandString); // 送信ヘルパーを利用
}
