/** Localizes speech resource preparation and controlled backend errors for settings and Pet.
 * No raw provider response is translated or forwarded; callers use fixed public error codes.
 * Voice engine configuration and SeedDuplex failures use the same language selection. */
const en={
 voiceMode:'Voice mode',voiceModeHint:'End voice before switching. Save and apply to restart the backend with the selected mode.',
 realtime_api_key_missing:'Enter a SeedDuplex API key, then save and apply.',realtime_voice_missing:'Enter a supported SeedDuplex voice ID, then save and apply.',
 realtime_connection_failed:'SeedDuplex could not connect. Check the key, voice ID and network, then restart voice.',
 realtime_disconnected:'Voice disconnected. Start voice again to reconnect.',realtime_protocol_error:'The voice response could not be processed. Start voice again.',
 realtime_provider_error:'SeedDuplex returned an error. Check your service configuration and restart voice.',
 realtime_context_unconfirmed:'The interrupted response could not be synchronized. Start voice again to continue.',
 realtime_text_disabled:'Close Realtime voice before sending a text message.',realtime_session_limit:'Start voice again to begin a new session.',
 speechResources:'Local speech resources',speechPrepare:'Prepare resources',speechPreparing:'Downloading…',speechReady:'Ready',speechMissing:'Missing or invalid',speechPrepared:'Resources prepared. Save and apply to use them.',speechCancelled:'Download cancelled. You can retry.',speechDownloadNetwork:'Download failed. Check your network and retry.',speechDownloadIntegrity:'Resource verification failed. Please retry.',speechDownloadFilesystem:'Cannot write resources. Check available space and directory permissions.',profilePreparing:'Preparing character. Please wait.',
 speech_disabled:'Enable voice in settings, then save and apply.',speech_api_key_missing:'Enter a DashScope key, then save and apply.',speech_optional_dependency_missing:'Speech dependencies are missing. Apply settings to install the speech extra.',speech_unavailable:'Voice could not start. Check speech settings and retry.',
 speech_vad_model_asset_missing:'VAD model missing. Prepare resources in voice settings.',speech_vad_license_asset_missing:'VAD license missing. Prepare resources in voice settings.',speech_vad_license_unverified:'VAD license is invalid. Prepare resources again.',speech_vad_model_load_failed:'VAD model could not load. Prepare resources again.',speech_vad_inference_failed:'VAD model is incompatible. Prepare resources again.',
 speech_eou_model_asset_missing:'Turn detection model missing. Prepare resources in voice settings.',speech_eou_license_asset_missing:'Turn detection license missing. Prepare resources in voice settings.',speech_eou_license_unverified:'Turn detection license is invalid. Prepare resources again.',speech_eou_model_load_failed:'Turn detection model could not load. Prepare resources again.',speech_eou_inference_failed:'Turn detection model is incompatible. Prepare resources again.',
};
const zh:Record<keyof typeof en,string>={
 voiceMode:'语音模式',voiceModeHint:'请结束语音后切换；保存并应用配置后，后端会以所选模式重新启动。',
 realtime_api_key_missing:'请填写 SeedDuplex API 密钥，然后保存并应用。',realtime_voice_missing:'请填写支持的 SeedDuplex 音色 ID，然后保存并应用。',
 realtime_connection_failed:'SeedDuplex 连接失败，请检查密钥、音色 ID 和网络后重新开启语音。',
 realtime_disconnected:'语音连接已断开，请重新开启语音。',realtime_protocol_error:'语音响应无法处理，请重新开启语音。',
 realtime_provider_error:'SeedDuplex 返回错误，请检查服务配置后重新开启语音。',
 realtime_context_unconfirmed:'被打断的回复未能同步，请重新开启语音继续。',
 realtime_text_disabled:'请先关闭 Realtime 语音，再发送文字消息。',realtime_session_limit:'请重新开启语音，开始新的会话。',
 speechResources:'本地语音资源',speechPrepare:'一键准备',speechPreparing:'正在下载…',speechReady:'已就绪',speechMissing:'缺失或未通过校验',speechPrepared:'资源已准备，请保存并应用。',speechCancelled:'下载已取消，可以重试。',speechDownloadNetwork:'下载失败，请检查网络后重试。',speechDownloadIntegrity:'资源校验失败，请重新准备。',speechDownloadFilesystem:'无法写入资源，请检查磁盘空间和目录权限。',profilePreparing:'正在准备角色，请稍候。',
 speech_disabled:'请在语音设置中启用语音，然后保存并应用。',speech_api_key_missing:'请填写 DashScope 密钥，然后保存并应用。',speech_optional_dependency_missing:'缺少语音运行依赖，请应用配置安装 speech 依赖。',speech_unavailable:'语音启动失败，请检查语音配置后重试。',
 speech_vad_model_asset_missing:'缺少 VAD 模型，请到语音设置一键准备资源。',speech_vad_license_asset_missing:'缺少 VAD 许可证，请到语音设置一键准备资源。',speech_vad_license_unverified:'VAD 许可证无效，请重新准备资源。',speech_vad_model_load_failed:'VAD 模型加载失败，请重新准备资源。',speech_vad_inference_failed:'VAD 模型不兼容，请重新准备资源。',
 speech_eou_model_asset_missing:'缺少断句模型，请到语音设置一键准备资源。',speech_eou_license_asset_missing:'缺少断句许可证，请到语音设置一键准备资源。',speech_eou_license_unverified:'断句许可证无效，请重新准备资源。',speech_eou_model_load_failed:'断句模型加载失败，请重新准备资源。',speech_eou_inference_failed:'断句模型不兼容，请重新准备资源。',
};
const ja:Record<keyof typeof en,string>={
 voiceMode:'音声モード',voiceModeHint:'音声を終了してから切り替えてください。保存・適用すると、選択したモードでバックエンドが再起動します。',
 realtime_api_key_missing:'SeedDuplex API キーを入力し、保存・適用してください。',realtime_voice_missing:'対応する SeedDuplex 音声 ID を入力し、保存・適用してください。',
 realtime_connection_failed:'SeedDuplex に接続できません。キー、音声 ID と接続を確認し、音声を再開してください。',
 realtime_disconnected:'音声が切断されました。音声を再開してください。',realtime_protocol_error:'音声応答を処理できません。音声を再開してください。',
 realtime_provider_error:'SeedDuplex エラーです。設定を確認し、音声を再開してください。',
 realtime_context_unconfirmed:'中断した応答を同期できません。音声を再開してください。',
 realtime_text_disabled:'文字を送信する前に Realtime 音声を終了してください。',realtime_session_limit:'音声を再開して新しいセッションを開始してください。',
 speechResources:'ローカル音声リソース',speechPrepare:'リソースを準備',speechPreparing:'ダウンロード中…',speechReady:'準備完了',speechMissing:'未取得または検証失敗',speechPrepared:'リソースを準備しました。保存して適用してください。',speechCancelled:'ダウンロードを中止しました。再試行できます。',speechDownloadNetwork:'ダウンロードに失敗しました。接続を確認してください。',speechDownloadIntegrity:'リソースの検証に失敗しました。再試行してください。',speechDownloadFilesystem:'書き込めません。空き容量とアクセス権を確認してください。',profilePreparing:'キャラクターを準備中です。',
 speech_disabled:'設定で音声を有効にして、保存・適用してください。',speech_api_key_missing:'DashScope キーを入力し、保存・適用してください。',speech_optional_dependency_missing:'音声の依存パッケージがありません。設定を適用してください。',speech_unavailable:'音声を開始できません。音声設定を確認してください。',
 speech_vad_model_asset_missing:'VAD モデルがありません。音声設定で準備してください。',speech_vad_license_asset_missing:'VAD ライセンスがありません。音声設定で準備してください。',speech_vad_license_unverified:'VAD ライセンスが無効です。再度準備してください。',speech_vad_model_load_failed:'VAD モデルを読み込めません。再度準備してください。',speech_vad_inference_failed:'VAD モデルに互換性がありません。再度準備してください。',
 speech_eou_model_asset_missing:'発話終了モデルがありません。音声設定で準備してください。',speech_eou_license_asset_missing:'発話終了モデルのライセンスがありません。音声設定で準備してください。',speech_eou_license_unverified:'発話終了モデルのライセンスが無効です。再度準備してください。',speech_eou_model_load_failed:'発話終了モデルを読み込めません。再度準備してください。',speech_eou_inference_failed:'発話終了モデルに互換性がありません。再度準備してください。',
};
export const SPEECH_TEXT={en,zh,ja};
