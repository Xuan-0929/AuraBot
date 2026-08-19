/**
 * 定义 Electron 对 health-only Python Backend 的状态投影。
 *
 * gateway 只保证 ok/service；该契约不再模拟已删除的端点注册表或前端地址，
 * 设置页只基于探测结果与固定 health 地址展示在线状态。
 */

export interface BackendGatewayHealth {
  ok: boolean;
  service: string;
}

export interface BackendStatus {
  online: boolean;
  checkedAt: string;
  gateway?: BackendGatewayHealth;
  error?: string;
}
