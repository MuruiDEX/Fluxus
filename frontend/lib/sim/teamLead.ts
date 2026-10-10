/**
 * Team Lead questioning engine — genuinely contextual (not a fixed form).
 * - Detects signals already present in the goal and SKIPS those questions.
 * - Later questions BRANCH on previous answers (e.g. resell → suppliers).
 */

export interface LeadAnswers {
  country?: string;
  city?: string;
  model?: string;
  budget?: string;
  audience?: string;
  extra?: string;
}

export interface LeadQuestion {
  key: keyof LeadAnswers;
  title: string;
  hint: string;
  placeholder: string;
  options?: string[];
}

const COUNTRIES = [
  "Poland",
  "Germany",
  "France",
  "Spain",
  "Netherlands",
  "United Kingdom",
  "United States",
];

const KNOWN_CITIES = [
  "Krakow",
  "Kraków",
  "Warsaw",
  "Gdansk",
  "Berlin",
  "London",
  "Paris",
  "Amsterdam",
];

export function extractSignals(goal: string): LeadAnswers {
  const g = goal.toLowerCase();
  const out: LeadAnswers = {};
  for (const c of COUNTRIES) {
    if (g.includes(c.toLowerCase())) {
      out.country = c === "Kraków" ? "Poland" : c;
      break;
    }
  }
  if (g.includes("krakow") || g.includes("kraków")) {
    out.country = "Poland";
    out.city = "Kraków";
  } else {
    for (const city of KNOWN_CITIES) {
      if (g.includes(city.toLowerCase())) {
        out.city = city;
        break;
      }
    }
  }
  if (/(manufacture|produce|make my own|own brand|own products)/.test(g)) out.model = "Own products";
  else if (/(resell|existing brands|dropship|wholesale)/.test(g)) out.model = "Resell brands";
  const budget = g.match(/\$ ?([\d,.]+)k?|(\d+)\s?(usd|eur|pln)/);
  if (budget) out.budget = budget[0].toUpperCase();
  if (/(women|men|kids|teen|athletes|professionals|tourists)/.test(g)) {
    const m = g.match(/for\s+([a-z\s]+?)(?:\s+in\s+|\s*$)/);
    if (m) out.audience = m[1].trim();
  }
  return out;
}

const QUESTION_BANK: Record<string, LeadQuestion> = {
  country: {
    key: "country",
    title: "Where will you operate?",
    hint: "Country determines pricing, competitors and regulations.",
    placeholder: "e.g. Poland",
    options: ["Poland", "Germany", "Spain", "Netherlands"],
  },
  city: {
    key: "city",
    title: "Which city should I focus on?",
    hint: "Local demand and rent differ per city.",
    placeholder: "e.g. Kraków",
  },
  model: {
    key: "model",
    title: "Will you create your own products or resell existing brands?",
    hint: "This changes costs, margins and the whole launch plan.",
    placeholder: "Own products or resell?",
    options: ["Own products", "Resell brands", "Hybrid"],
  },
  suppliers: {
    key: "extra",
    title: "Do you already have suppliers or brands in mind?",
    hint: "Only asked because you chose reselling — I can research alternatives.",
    placeholder: "e.g. No, please research",
  },
  budget: {
    key: "budget",
    title: "What is your starting budget?",
    hint: "I will size the team and AI spend to fit it.",
    placeholder: "e.g. $20,000",
    options: ["Under $5k", "$5–20k", "$20–50k", "$50k+"],
  },
  audience: {
    key: "audience",
    title: "Who is the offer for?",
    hint: "One sharp audience beats three vague ones.",
    placeholder: "e.g. Busy professionals 25–40",
  },
};

/**
 * Returns the next question to ask, or null when enough context is gathered.
 * Never asks about a key already answered or detected.
 */
export function nextQuestion(goal: string, answers: LeadAnswers): LeadQuestion | null {
  const known: LeadAnswers = { ...extractSignals(goal), ...answers };
  const isFitness = /fit|gym|studio|yoga|sport/.test(goal.toLowerCase());

  const order: (keyof LeadAnswers | "suppliers")[] = ["country"];
  if (isFitness && !known.city) order.push("city");
  order.push("model");
  if (known.model === "Resell brands" || known.model === "Hybrid") order.push("suppliers");
  order.push("budget", "audience");

  const answered = new Set(
    Object.entries(known)
      .filter(([, v]) => v && String(v).trim().length > 0)
      .map(([k]) => k),
  );

  for (const key of order) {
    if (key === "suppliers") {
      if (!known.extra) return QUESTION_BANK.suppliers;
      continue;
    }
    if (!answered.has(key as string)) {
      // Branch: skip city when country unknown? No — city implies country.
      if (key === "country" && known.city && !known.country) continue;
      return QUESTION_BANK[key as string];
    }
  }
  return null;
}

export function questionsAsked(goal: string, answers: LeadAnswers): number {
  return Object.values(answers).filter((v) => v && String(v).trim()).length;
}

export function buildBriefSummary(goal: string, answers: LeadAnswers): string[] {
  const known: LeadAnswers = { ...extractSignals(goal), ...answers };
  const lines: string[] = [`Goal: ${goal}`];
  if (known.country) lines.push(`Market: ${known.city ? `${known.city}, ` : ""}${known.country}`);
  if (known.model) lines.push(`Model: ${known.model}`);
  if (known.budget) lines.push(`Budget: ${known.budget}`);
  if (known.audience) lines.push(`Audience: ${known.audience}`);
  if (known.extra) lines.push(`Notes: ${known.extra}`);
  return lines;
}
