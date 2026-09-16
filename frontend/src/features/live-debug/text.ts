/** Localizes the independent Live debug window and its immediate settings controls.
 * Shared keys keep status, stage and structured field labels consistent across zh/en/ja;
 * unknown diagnostic fields retain their original names so new telemetry stays inspectable. */
const en = {
 title:'Live voice debug', toggle:'Live voice debug mode', hint:'Takes effect immediately. Logs save recognized speech and replies on this computer.',
 open:'Open debug window', clear:'Clear debug logs', folder:'Open log directory', autoScroll:'Auto-scroll', enabled:'Capturing', disabled:'Capture off',
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
 title:'Live 语音 Debug', toggle:'Live 语音 Debug 模式', hint:'立即生效。日志会在本机保存识别文本和回复文本。',
 open:'打开 Debug 窗口', clear:'清空 Debug 日志', folder:'打开日志目录', autoScroll:'自动滚动', enabled:'正在采集', disabled:'采集已关闭',
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
 title:'Live 音声 Debug', toggle:'Live 音声 Debug モード', hint:'すぐに反映されます。認識テキストと応答テキストをこの端末のログに保存します。',
 open:'Debug ウィンドウを開く', clear:'Debug ログを消去', folder:'ログフォルダーを開く', autoScroll:'自動スクロール', enabled:'記録中', disabled:'記録停止',
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
export function debugText(language:string,key:string):string {
 const dictionary = language==='ja'?ja:language==='en'?en:zh;
 return dictionary[(aliases[key] ?? key) as DebugTextKey] ?? key;
}
