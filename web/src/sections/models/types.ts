export type Provider = {
  id: string;
  label: string;
  key: boolean;
  base_url: string;
};

export type AgentKind = {
  id: string;
  label: string;
  model: boolean;
};

export type Endpoint = {
  id: string;
  name: string;
  provider: string;
  base_url: string;
  has_api_key: boolean;
  model_names: string[];
};

export type OrgModel = {
  id: string;
  name: string;
  provider: string;
  model_name: string;
  endpoint_id: string;
  endpoint_name: string;
  enabled: boolean;
};

export type OrgAgent = {
  id: string;
  name: string;
  kind: string;
  model_id: string;
  enabled: boolean;
};

export type OrgCatalog = {
  can_manage?: boolean;
  endpoint_warning?: string;
  providers?: Provider[];
  agent_kinds?: AgentKind[];
  endpoints?: Endpoint[];
  models?: OrgModel[];
  agents?: OrgAgent[];
};
