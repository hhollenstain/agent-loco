export type Skill = {
  name: string;
  origin?: string;
  source_url?: string;
  summary?: string;
  description?: string;
  enabled?: boolean;
  assigned?: boolean;
  html?: string;
  body?: string;
};

export type SkillSource = {
  id?: string;
  slug?: string;
  url: string;
  sha?: string;
  scope_type?: string;
  can_sync?: boolean;
};

export type SkillTeam = {
  id: string;
  name: string;
};

export type SkillsPayload = {
  skills?: Skill[];
  sources?: SkillSource[];
  assigned?: string[];
  enabled?: string[];
  skill_teams?: SkillTeam[];
};
