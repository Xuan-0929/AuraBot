export interface BackendGatewayHealth {
  ok: boolean;
  service: string;
  host: string;
  port: number;
  frontend: string;
}

export interface BackendServiceEndpoint {
  name: string;
  host: string;
  port: number;
  url: string;
}

export interface BackendStatus {
  online: boolean;
  checkedAt: string;
  gateway?: BackendGatewayHealth;
  services: BackendServiceEndpoint[];
  error?: string;
}
