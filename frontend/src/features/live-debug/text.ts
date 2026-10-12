/** Localizes the independent Live debug window and its launch-gated settings actions.
 * Shared keys keep status, stage and structured field labels consistent across zh/en/ja;
 * field labels include acoustic windows, VAD-only onset and current-gate counts, readiness and STT teardown context;
 * inspector controls leave unknown keys inspectable without altering telemetry values. */
const en = {
 title:'Live voice debug', launchMode:'Started in debug mode', hint:'Enabled for this application run. Logs save recognized speech and replies on this computer.',
 open:'Open debug window', clear:'Clear debug logs', folder:'Open log directory', autoScroll:'Follow latest', enabled:'Capturing', disabled:'Capture off',
 state:'Current state', events:'Events', source:'Source', stage:'Stage', level:'Level', all:'All', search:'Search events', empty:'No events',
 newer:'Newer', older:'Older', latest:'Latest', dropped:'Dropped', stable:'Stable transcript', mutable:'Mutable transcript', reply:'Reply',
 connection:'Connection', input:'Input', stt:'Speech recognition', eou:'End of utterance', model:'Model', tts:'Speech synthesis', playback:'Playback',
 transport:'Pet transport', capture:'Microphone capture', devicePlayback:'Device playback', backend:'Backend', pet:'Pet', main:'Desktop',
 info:'Info', warning:'Warning', error:'Error', timestamp:'Time', status:'Status', name:'Event', text:'Text', stable_text:'Stable text', mutable_text:'Mutable text',
 final:'Final', generation:'Generation', segmentId:'Segment', inputId:'Input', invokeId:'Invocation', sayId:'Speech', clauseId:'Clause',
 duration_ms:'Duration (ms)', elapsed_ms:'Elapsed (ms)', latency_ms:'Latency (ms)', reason:'Reason', queue_depth:'Queue depth', active:'Active',
 truncated:'Truncated', logDirectory:'Log directory', details:'Details', waiting:'Waiting for events', failure:'Operation failed',
} as const;
type DebugTextKey = keyof typeof en;
const zh:Record<DebugTextKey,string> = {
 title:'Live 语音 Debug', launchMode:'本次以调试模式启动', hint:'仅本次运行生效。日志会在本机保存识别文本和回复文本。',
 open:'打开 Debug 窗口', clear:'清空 Debug 日志', folder:'打开日志目录', autoScroll:'跟随最新', enabled:'正在采集', disabled:'采集已关闭',
 state:'当前状态', events:'事件', source:'来源', stage:'阶段', level:'级别', all:'全部', search:'搜索事件', empty:'暂无事件',
 newer:'较新', older:'更早', latest:'最新', dropped:'已丢弃', stable:'稳定识别文本', mutable:'可变识别文本', reply:'回复文本',
 connection:'连接', input:'输入', stt:'语音识别', eou:'话轮结束检测', model:'模型', tts:'语音合成', playback:'播放',
 transport:'桌宠传输', capture:'麦克风采集', devicePlayback:'设备播放', backend:'后端', pet:'桌宠', main:'桌面主进程',
 info:'信息', warning:'警告', error:'错误', timestamp:'时间', status:'状态', name:'事件', text:'文本', stable_text:'稳定文本', mutable_text:'可变文本',
 final:'已完成', generation:'代次', segmentId:'片段', inputId:'输入', invokeId:'调用', sayId:'语音', clauseId:'分句',
 duration_ms:'时长（毫秒）', elapsed_ms:'耗时（毫秒）', latency_ms:'延迟（毫秒）', reason:'原因', queue_depth:'队列长度', active:'活动中',
 truncated:'已截断', logDirectory:'日志目录', details:'详情', waiting:'等待事件', failure:'操作失败',
};
const ja:Record<DebugTextKey,string> = {
 title:'Live 音声 Debug', launchMode:'デバッグモードで起動中', hint:'今回の起動でのみ有効です。認識テキストと応答テキストをこの端末のログに保存します。',
 open:'Debug ウィンドウを開く', clear:'Debug ログを消去', folder:'ログフォルダーを開く', autoScroll:'最新を追跡', enabled:'記録中', disabled:'記録停止',
 state:'現在の状態', events:'イベント', source:'ソース', stage:'段階', level:'レベル', all:'すべて', search:'イベントを検索', empty:'イベントなし',
 newer:'新しい', older:'古い', latest:'最新', dropped:'破棄', stable:'確定認識テキスト', mutable:'暫定認識テキスト', reply:'応答テキスト',
 connection:'接続', input:'入力', stt:'音声認識', eou:'発話終了検出', model:'モデル', tts:'音声合成', playback:'再生',
 transport:'ペット通信', capture:'マイク収録', devicePlayback:'端末再生', backend:'バックエンド', pet:'ペット', main:'デスクトップ',
 info:'情報', warning:'警告', error:'エラー', timestamp:'時刻', status:'状態', name:'イベント', text:'テキスト', stable_text:'確定テキスト', mutable_text:'暫定テキスト',
 final:'完了', generation:'世代', segmentId:'区間', inputId:'入力', invokeId:'呼び出し', sayId:'音声', clauseId:'文節',
 duration_ms:'長さ（ms）', elapsed_ms:'所要時間（ms）', latency_ms:'遅延（ms）', reason:'理由', queue_depth:'キュー長', active:'動作中',
 truncated:'省略あり', logDirectory:'ログフォルダー', details:'詳細', waiting:'イベント待機中', failure:'操作に失敗しました',
};
const aliases:Record<string,DebugTextKey>={stablePrefix:'stable_text',mutableSuffix:'mutable_text',stableText:'stable_text',mutableText:'mutable_text',elapsedMs:'elapsed_ms',durationMs:'duration_ms',latencyMs:'latency_ms',queueDepth:'queue_depth',updatedAt:'timestamp'};
// Columns are English, Chinese and Japanese; raw keys remain available in field tooltips.
const inspectorLabels:Record<string,string[]> = Object.fromEntries(`
view|View|视图|表示
inspector|Inspector|检查面板|インスペクター
conversation|Conversation text|对话文字|会話テキスト
eventDetails|Event details|事件详情|イベント詳細
matchingRetained|matched / retained|匹配／保留|一致／保持
newEvents|New events:|新事件：|新着：
historyExpired|Some earlier records have left memory.|部分较早记录已移出内存。|以前の記録の一部はメモリから削除されました。
selectedExpired|This event is no longer in the list. Its selected details are retained here.|此事件已移出列表，所选详情仍保留在此处。|このイベントは一覧から削除されました。選択した詳細はここに保持されています。
selectEvent|Select an event to read its complete details.|选择一条事件查看完整详情。|イベントを選択すると詳細を表示します。
missing|Not captured this time|本次未采集|今回未記録
yes|Yes|是|はい
no|No|否|いいえ
sessionGroup|Session & connection|会话与连接|セッションと接続
inputGroup|Input & microphone|输入与麦克风|入力とマイク
recognitionGroup|Recognition & end of utterance|识别与断句|認識と発話終了
modelGroup|Model processing|模型处理|モデル処理
outputGroup|Synthesis & delivery|合成与发送|合成と送信
deviceGroup|Device playback|设备播放|端末再生
id|Timeline ID|时间线序号|タイムライン ID
seq|Source sequence|来源序号|ソース連番
instanceId|Process instance|进程实例|プロセスインスタンス
captureId|Capture batch|采集批次|記録バッチ
profileId|Profile|角色 Profile|プロフィール
configRevision|Configuration revision|配置版本|設定リビジョン
system|System|系统|システム
enabled|Capturing|正在采集|記録中
voiceEnabled|Voice enabled|语音已开启|音声有効
code|Result code|结果码|結果コード
errorType|Error type|错误类型|エラー種類
exceptionType|Exception type|异常类型|例外種類
providerEventKind|Provider event type|服务商事件类型|プロバイダーイベント
providerStability|Provider stability|服务商稳定性|プロバイダー安定度
sent|Sent|已发送|送信済み
received|Received|已接收|受信済み
frames|Frames|帧数|フレーム数
sentFrames|Sent frames|已发送帧数|送信フレーム数
droppedFrames|Dropped frames|丢弃帧数|破棄フレーム数
receivedFrames|Received frames|已接收帧数|受信フレーム数
consumedSamples|Consumed samples|已消耗采样数|消費サンプル数
sentSamples|Sent samples|已发送采样数|送信サンプル数
queuedSamples|Queued samples|排队采样数|待機サンプル数
queuedMs|Queued duration|待播时长|待機時間
bufferedAmount|Transport buffer|传输缓冲量|通信バッファ
sampleRate|Sample rate|采样率|サンプルレート
channelCount|Channels|声道数|チャンネル数
echoCancellation|Echo cancellation|回声消除|エコー除去
noiseSuppression|Noise suppression|降噪|ノイズ抑制
autoGainControl|Automatic gain|自动增益|自動ゲイン
firstChunkMs|First chunk latency|首片段耗时|最初の断片まで
firstAudioMs|First audio latency|首包音频耗时|最初の音声まで
contextTime|Audio context time|音频上下文时间|音声コンテキスト時刻
endContextTime|End context time|结束上下文时间|終了コンテキスト時刻
outputTime|Output time|输出时间|出力時刻
outputContextTime|Output context time|输出上下文时间|出力コンテキスト時刻
clockSkewMs|Clock offset|时钟偏差|クロック偏差
mode|Mode|模式|モード
phase|Phase|阶段|フェーズ
accepted|Accepted|已接受|受理済み
complete|Complete|已完成|完了
cancelled|Cancelled|已取消|キャンセル済み
available|Available|可用|利用可能
rms|RMS|均方根 RMS|RMS
noise|Noise level|噪声值|ノイズ値
noiseFloor|Noise floor|噪声基线|ノイズフロア
threshold|Threshold|门限|しきい値
vad|VAD probability|VAD 概率|VAD 確率
probability|Probability|概率|確率
vadThreshold|VAD threshold|VAD 门限|VAD しきい値
onsetGate|Onset confirmation gate|讲话开始确认规则|発話開始の確認条件
eouThreshold|EOU threshold|EOU 门限|EOU しきい値
speechMs|Speech duration|讲话时长|発話時間
silenceMs|Silence duration|静音时长|無音時間
confirmMs|Confirmation duration|连续确认时长|確認時間
windowMs|Window duration|窗口时长|ウィンドウ時間
windowFrames|Window frames|窗口帧数|ウィンドウフレーム数
vadPassFrames|VAD passing frames|VAD 达标帧数|VAD 判定通過フレーム数
rmsPassFrames|RMS passing frames|音量达标帧数|音量判定通過フレーム数
qualifiedFrames|Frames passing current gate|当前规则达标帧数|現在の条件を通過したフレーム数
maxQualifiedRunMs|Longest passing run in window|窗口内最长连续达标时长|ウィンドウ内最長連続通過時間
maxConfirmMs|Peak confirmation duration|窗口内最大确认时长|最大確認時間
rmsMin|Minimum RMS|最低 RMS|最小 RMS
rmsMax|Maximum RMS|最高 RMS|最大 RMS
thresholdMin|Minimum volume threshold|最低音量门槛|最小音量しきい値
thresholdMax|Maximum volume threshold|最高音量门槛|最大音量しきい値
resetRmsCount|RMS resets|音量不足清零次数|音量不足リセット回数
resetVadCount|VAD resets|VAD 不足清零次数|VAD 不足リセット回数
resetBothCount|Joint failure resets|两者不足清零次数|両方不足リセット回数
resetUnavailableCount|Unavailable VAD resets|VAD 无结果清零次数|VAD 結果なしリセット回数
lastResetReason|Last reset reason|最后清零原因|最後のリセット理由
lastResetFromMs|Confirmation lost on last reset|最后清零前确认时长|最後に失われた確認時間
vadStatus|VAD status|VAD 状态|VAD 状態
vadReason|VAD status reason|VAD 状态原因|VAD 状態の理由
vadWarmupFrames|VAD warmup frames|VAD 预热帧数|VAD ウォームアップフレーム数
vadUnavailableFrames|VAD unavailable frames|VAD 不可用帧数|VAD 利用不可フレーム数
vadErrorFrames|VAD error frames|VAD 推理错误帧数|VAD 推論エラーフレーム数
operation|Operation|操作|操作
finalizing|Finalizing STT session|正在收尾转写会话|音声認識終了処理中
commandId|Command ID|命令标识|コマンド ID
commandType|Command type|命令类型|コマンド種類
revision|Revision|版本|リビジョン
contextVersion|Context version|上下文版本|コンテキスト版
trigger|Trigger|触发原因|トリガー
highPriority|High priority|高优先级|高優先度
admitted|Admitted|已准入|許可済み
count|Count|数量|件数
bytes|Bytes|字节数|バイト数
samples|Samples|采样数|サンプル数
totalBytes|Total bytes|总字节数|合計バイト数
totalSamples|Total samples|总采样数|合計サンプル数
toolName|Tool name|工具名称|ツール名
receiptCode|Receipt code|回执码|受領コード
silent|Silent|无回复|応答なし
startedAt|Started at|开始时间|開始時刻
deadlineMs|Deadline|截止时长|期限
fallbackMs|Fallback duration|兜底时长|フォールバック時間
online|Online|在线|オンライン
connected|Connected|已连接|接続済み
audioContextState|Audio context state|音频上下文状态|音声コンテキスト状態
settled|Settled|已结算|確定済み
draining|Draining|正在排空|排出中
paused|Paused|已暂停|一時停止
blocked|Blocked|已阻塞|ブロック中
audioFinished|Audio finished|音频已结束|音声終了
audioCancelled|Audio cancelled|音频已取消|音声キャンセル
inputActive|Input active|输入活动中|入力中
workState|Work state|工作状态|処理状態
pendingClauses|Pending clauses|待处理分句数|保留文節数
captureActive|Capture active|采集中|収録中
contextState|Audio context state|音频上下文状态|音声コンテキスト状態
playbackBlocked|Playback blocked|播放阻塞|再生ブロック
hasClause|Has clause|存在分句|文節あり
started|Started|已开始|開始済み
role|Role|角色|役割
micSentFrames|Microphone sent frames|麦克风发送帧数|マイク送信フレーム
micDroppedFrames|Microphone dropped frames|麦克风丢帧数|マイク破棄フレーム
micSentSamples|Microphone sent samples|麦克风发送采样数|マイク送信サンプル
micDroppedSamples|Microphone dropped samples|麦克风丢弃采样数|マイク破棄サンプル
receivedSamples|Received samples|已接收采样数|受信サンプル数
accepting|Accepting audio|正在接收音频|音声受付中
queuedFrames|Queued frames|排队帧数|待機フレーム
speech|Speech detected|检测到讲话|発話検出
confirmTargetMs|Confirmation target|确认目标时长|確認目標時間
silenceTargetMs|Silence target|静音目标时长|無音目標時間
timeoutMs|Timeout|超时时长|タイムアウト
incomplete|Incomplete|未完成|未完了
pending|Pending|待处理|保留中
running|Running|运行中|実行中
queuedClauses|Queued clauses|排队分句数|待機文節数
hasTranscript|Has transcript|存在转写|認識結果あり
endpointPending|Endpoint pending|等待断句|発話終了待ち
speechCandidate|Speech candidate|候选讲话|発話候補
confirmedSpeech|Confirmed speech|已确认讲话|発話確認済み
prepareCount|Preparation count|准备次数|準備回数
firstAudio|First audio|首包音频|最初の音声
partial|Partial|部分|部分
kind|Kind|类型|種類
eventKind|Event kind|事件类型|イベント種類
stability|Stability|稳定性|安定度
consumed|Consumed|已消耗|消費済み
sentBytes|Sent bytes|已发送字节数|送信バイト数
receivedBytes|Received bytes|已接收字节数|受信バイト数
microphoneState|Microphone state|麦克风状态|マイク状態
backendOnline|Backend online|后端在线|バックエンド接続
connectionState|Connection state|连接状态|接続状態
recording|Recording|正在记录|記録中
truncation|Truncation|截断|切り詰め
error|Error|错误|エラー
droppedEvents|Dropped events|丢弃事件数|破棄イベント数
`.trim().split('\n').map(line=>{const [key,...values]=line.split('|');return [key!,values];}));
export function debugText(language:string,key:string):string {
 const dictionary = language==='ja'?ja:language==='en'?en:zh;
 return inspectorLabels[key]?.[language==='ja'?2:language==='en'?0:1] ?? dictionary[(aliases[key] ?? key) as DebugTextKey] ?? key;
}
