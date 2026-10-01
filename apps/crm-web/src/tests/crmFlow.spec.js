// One end-to-end flow through the whole CRM, at component level: the real app,
// router and stores, with `fetch` answered by an in-memory stand-in for the API
// that returns what the real API would at each step.
import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VTextField } from "vuetify/components";
import App from "../App.vue";
import { vuetify } from "../plugins/vuetify";
import { router } from "../router";

// jsdom has no canvas; the forecast's chart data is covered in forecast.spec.js.
vi.mock("vue-chartjs", () => ({
  Bar: { name: "Bar", props: ["data", "options", "plugins"], template: "<div />" },
}));

const OPEN_STAGES = ["prospecting", "qualification", "proposal", "negotiation"];
const PROBABILITY = {
  prospecting: 10,
  qualification: 25,
  proposal: 50,
  negotiation: 75,
  closed_won: 100,
  closed_lost: 0,
};
const TODAY = "2026-10-01";

function money(value) {
  return Number(value).toFixed(2);
}

function addDays(iso, days) {
  const date = new Date(`${iso}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function quarterRange(label) {
  const [year, q] = label.split("-Q").map(Number);
  const first = (q - 1) * 3;
  const end = new Date(Date.UTC(year, first + 3, 0)).toISOString().slice(0, 10);
  return { start: `${year}-${String(first + 1).padStart(2, "0")}-01`, end };
}

// The parts of the API this flow touches, following its schemas and rules.
function fakeApi() {
  const reps = [
    {
      id: 1,
      name: "Avery Lee",
      email: "avery.lee@pragmattie-sync.example",
      region: "EMEA",
      quarterly_quota: "500000.00",
    },
    {
      id: 2,
      name: "Jordan Park",
      email: "jordan.park@pragmattie-sync.example",
      region: "APAC",
      quarterly_quota: "500000.00",
    },
  ];
  const owner = (id) => {
    const rep = reps.find((r) => r.id === id);
    return rep ? { id: rep.id, name: rep.name } : null;
  };
  const db = { leads: [], accounts: [], contacts: [], opportunities: [], nextId: 100 };
  const requests = [];

  const opportunityOut = (o) => {
    const account = db.accounts.find((a) => a.id === o.account_id);
    return { ...o, account: { id: account.id, name: account.name }, owner: owner(o.owner_id) };
  };
  const leadOut = (lead) => ({ ...lead, owner: owner(lead.owner_id) });

  function forecast(label) {
    const { start, end } = quarterRange(label);
    const deals = db.opportunities.filter((o) => o.close_date >= start && o.close_date <= end);
    const totals = (list) => {
      const sum = (stage) =>
        list.filter((d) => d.stage === stage).reduce((t, d) => t + Number(d.amount), 0);
      const won = sum("closed_won");
      const commit = won + sum("negotiation");
      const weighted =
        won +
        list
          .filter((d) => OPEN_STAGES.includes(d.stage))
          .reduce((t, d) => t + (Number(d.amount) * d.probability) / 100, 0);
      return { won, commit, best_case: commit + sum("proposal"), weighted };
    };
    const all = totals(deals);
    const months = [0, 1, 2].map((i) => {
      const monthNumber = Number(start.slice(5, 7)) + i;
      const month = `${start.slice(0, 5)}${String(monthNumber).padStart(2, "0")}`;
      const t = totals(deals.filter((d) => d.close_date.startsWith(month)));
      return { month, ...Object.fromEntries(Object.entries(t).map(([k, v]) => [k, money(v)])) };
    });
    return {
      quarter: label,
      start,
      end,
      quota: money(reps.reduce((t, r) => t + Number(r.quarterly_quota), 0)),
      won: money(all.won),
      commit: money(all.commit),
      best_case: money(all.best_case),
      pipeline: money(
        deals
          .filter((d) => OPEN_STAGES.includes(d.stage))
          .reduce((t, d) => t + Number(d.amount), 0),
      ),
      weighted: money(all.weighted),
      by_month: months,
      by_rep: reps.map((rep) => {
        const t = totals(deals.filter((d) => d.owner_id === rep.id));
        return {
          rep: { id: rep.id, name: rep.name },
          quota: rep.quarterly_quota,
          won: money(t.won),
          commit: money(t.commit),
          weighted: money(t.weighted),
          attainment_pct: Math.round((t.won / Number(rep.quarterly_quota)) * 1000) / 10,
        };
      }),
      by_stage: OPEN_STAGES.map((stage) => {
        const list = deals.filter((d) => d.stage === stage);
        return {
          stage,
          count: list.length,
          amount: money(list.reduce((t, d) => t + Number(d.amount), 0)),
        };
      }),
    };
  }

  function handle(method, url, body) {
    const path = url.pathname;
    const query = url.searchParams;
    let match;

    if (method === "GET" && path === "/api/v1/reps") return [200, reps];

    if (method === "GET" && path === "/api/v1/leads") {
      const statuses = query.getAll("status");
      const items = db.leads
        .filter((lead) => !statuses.length || statuses.includes(lead.status))
        .toReversed()
        .map(leadOut);
      return [200, { items, total: items.length, limit: 25, offset: 0 }];
    }

    if (method === "POST" && path === "/api/v1/leads") {
      const lead = {
        id: db.nextId++,
        ...body,
        status: "new",
        converted_account_id: null,
        created_at: `${TODAY}T09:00:00`,
      };
      db.leads.push(lead);
      return [201, leadOut(lead)];
    }

    if (method === "POST" && (match = path.match(/^\/api\/v1\/leads\/(\d+)\/convert$/))) {
      const lead = db.leads.find((l) => l.id === Number(match[1]));
      if (lead.status === "converted") return [409, { detail: "Lead is already converted" }];
      const account = {
        id: db.nextId++,
        name: lead.company,
        industry: body.industry,
        employee_count: body.employee_count,
        annual_revenue: "0.00",
        region: body.region,
        website: null,
        owner_id: lead.owner_id,
        created_at: `${TODAY}T09:05:00`,
      };
      const contact = {
        id: db.nextId++,
        account_id: account.id,
        first_name: lead.first_name,
        last_name: lead.last_name,
        email: lead.email,
        title: lead.title,
        phone: null,
        created_at: account.created_at,
      };
      db.accounts.push(account);
      db.contacts.push(contact);
      let opportunity = null;
      if (body.opportunity_amount != null) {
        opportunity = {
          id: db.nextId++,
          account_id: account.id,
          name: body.opportunity_name || `${lead.company} - New business`,
          amount: money(body.opportunity_amount),
          stage: "qualification",
          probability: PROBABILITY.qualification,
          close_date: addDays(TODAY, 60),
          owner_id: lead.owner_id,
          created_at: account.created_at,
        };
        db.opportunities.push(opportunity);
      }
      lead.status = "converted";
      lead.converted_account_id = account.id;
      return [
        200,
        {
          lead: leadOut(lead),
          account_id: account.id,
          contact_id: contact.id,
          opportunity_id: opportunity?.id ?? null,
        },
      ];
    }

    if (method === "GET" && (match = path.match(/^\/api\/v1\/accounts\/(\d+)$/))) {
      const account = db.accounts.find((a) => a.id === Number(match[1]));
      if (!account) return [404, { detail: `Account ${match[1]} not found` }];
      const opportunities = db.opportunities.filter((o) => o.account_id === account.id);
      const contacts = db.contacts.filter((c) => c.account_id === account.id);
      return [
        200,
        {
          ...account,
          owner: owner(account.owner_id),
          open_pipeline: money(
            opportunities
              .filter((o) => OPEN_STAGES.includes(o.stage))
              .reduce((t, o) => t + Number(o.amount), 0),
          ),
          contact_count: contacts.length,
          contacts,
          opportunities: opportunities.map(opportunityOut),
        },
      ];
    }

    if (method === "GET" && path === "/api/v1/opportunities") {
      const stages = query.getAll("stage");
      const from = query.get("close_from");
      const to = query.get("close_to");
      const ownerId = query.get("owner_id");
      const items = db.opportunities
        .filter((o) => !stages.length || stages.includes(o.stage))
        .filter((o) => (!from || o.close_date >= from) && (!to || o.close_date <= to))
        .filter((o) => !ownerId || o.owner_id === Number(ownerId))
        .map(opportunityOut);
      return [200, { items, total: items.length, limit: 500, offset: 0 }];
    }

    if (method === "PATCH" && (match = path.match(/^\/api\/v1\/opportunities\/(\d+)$/))) {
      const opportunity = db.opportunities.find((o) => o.id === Number(match[1]));
      if (body.stage && body.stage !== opportunity.stage) {
        opportunity.probability = PROBABILITY[body.stage];
      }
      Object.assign(opportunity, body);
      return [200, opportunityOut(opportunity)];
    }

    if (method === "GET" && path === "/api/v1/forecast") {
      return [200, forecast(query.get("quarter"))];
    }

    return [404, { detail: "Not Found" }];
  }

  const fetch = vi.fn(async (input, options = {}) => {
    const url = new URL(input);
    const method = options.method ?? "GET";
    const body = options.body ? JSON.parse(options.body) : undefined;
    requests.push({ method, path: url.pathname, body });
    const [status, payload] = handle(method, url, body);
    return {
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(structuredClone(payload)),
    };
  });

  return { fetch, db, requests };
}

let api;
let wrapper;

enableAutoUnmount(afterEach);

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 9, 1, 12));
  api = fakeApi();
  vi.stubGlobal("fetch", api.fetch);
  await router.push("/");
  await router.isReady();
  wrapper = mount(App, {
    attachTo: document.body,
    global: { plugins: [createPinia(), router, vuetify] },
  });
  await flushPromises();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

function body(selector) {
  return document.body.querySelector(selector);
}

async function settle() {
  await flushPromises();
  await flushPromises();
}

async function openSection(title) {
  const link = wrapper.findAll(".v-navigation-drawer a").find((a) => a.text() === title);
  await link.trigger("click");
  await vi.waitFor(() => expect(wrapper.find("h1").text()).toBe(title));
  await settle();
}

function textField(name) {
  return wrapper.findAllComponents(VTextField).find((field) => field.props("name") === name);
}

function lastRequest(method, pathPrefix) {
  return api.requests.filter((r) => r.method === method && r.path.startsWith(pathPrefix)).at(-1);
}

describe("CRM flow: lead to closed-won forecast", () => {
  it("creates, converts and wins a deal, then sees it in the forecast", async () => {
    // 1. Open Leads and create a lead.
    await openSection("Leads");
    expect(wrapper.text()).toContain("No leads match these filters");

    await wrapper.find('[data-test="new-lead"]').trigger("click");
    await settle();
    textField("first_name").vm.$emit("update:modelValue", "Sam");
    textField("last_name").vm.$emit("update:modelValue", "Lee");
    textField("email").vm.$emit("update:modelValue", "sam.lee@contoso.example");
    textField("company").vm.$emit("update:modelValue", "Contoso Example");
    wrapper
      .findComponent({ name: "NewLeadDialog" })
      .findAllComponents({ name: "VSelect" })[1]
      .vm.$emit("update:modelValue", 1);
    await settle();
    body('.v-dialog [data-test="save"]').click();
    await settle();

    expect(lastRequest("POST", "/api/v1/leads").body).toMatchObject({
      company: "Contoso Example",
      owner_id: 1,
    });
    expect(document.body.textContent).toContain("Lead created for Contoso Example");
    await vi.waitFor(() => expect(wrapper.find("tbody").text()).toContain("Sam Lee"));
    expect(wrapper.find("tbody").text()).toContain("New");

    // 2. Convert it with a deal.
    await wrapper.find('[data-test="actions"]').trigger("click");
    await settle();
    body('[data-test="convert"]').click();
    await settle();
    expect(body('[data-test="convert-dialog"]').textContent).toContain("Convert Sam Lee");
    textField("opportunity_amount").vm.$emit("update:modelValue", 80000);
    await settle();
    body('[data-test="submit"]').click();
    await settle();

    const convert = lastRequest("POST", "/api/v1/leads/");
    expect(convert.path).toBe(`/api/v1/leads/${api.db.leads[0].id}/convert`);
    expect(convert.body).toEqual({
      industry: "Technology",
      region: "North America East",
      employee_count: 100,
      opportunity_name: "Contoso Example - New business",
      opportunity_amount: 80000,
    });

    // 3. Land on the account page and see the deal.
    const accountId = api.db.accounts[0].id;
    await vi.waitFor(() =>
      expect(router.currentRoute.value.fullPath).toBe(`/accounts/${accountId}`),
    );
    await vi.waitFor(() => expect(wrapper.find("h1").text()).toBe("Contoso Example"));
    await settle();
    expect(wrapper.text()).toContain("Technology · North America East");
    const deal = wrapper.find('[data-test="opportunities"] tbody tr');
    expect(deal.text()).toContain("Contoso Example - New business");
    expect(deal.text()).toContain("Qualification");
    expect(deal.text()).toContain("$80,000");
    expect(deal.text()).toContain("Nov 30, 2026");
    expect(wrapper.find('[data-test="contacts"]').text()).toContain("Sam Lee");
    expect(
      wrapper.find('[data-test="tile-open-pipeline"] [data-test="tile-value"]').text(),
    ).toBe("$80K");

    // 4. Open Pipeline and move the deal to Closed won.
    await openSection("Pipeline");
    const column = wrapper.find('[data-test="column-qualification"]');
    expect(column.find('[data-test="deal-name"]').text()).toBe("Contoso Example - New business");
    expect(wrapper.find('[data-test="summary-open"]').text()).toBe("$80,000");
    expect(wrapper.find('[data-test="summary-weighted"]').text()).toBe("$20,000");

    await wrapper.find('[data-test="deal-actions"]').trigger("click");
    await settle();
    body('[data-test="move-closed_won"]').click();
    await settle();

    expect(lastRequest("PATCH", "/api/v1/opportunities/").body).toEqual({ stage: "closed_won" });
    expect(api.db.opportunities[0]).toMatchObject({ stage: "closed_won", probability: 100 });
    expect(document.body.textContent).toContain(
      "Contoso Example - New business moved to Closed won",
    );
    expect(wrapper.findAll('[data-test="deal"]')).toHaveLength(0);
    expect(wrapper.find('[data-test="summary-count"]').text()).toBe("0");

    // 5. Open Forecast and see it counted in closed won.
    await openSection("Forecast");
    expect(lastRequest("GET", "/api/v1/forecast")).toBeTruthy();
    expect(api.fetch.mock.calls.at(-1)[0]).toContain("quarter=2026-Q4");
    expect(wrapper.find("[data-test='tile-won'] [data-test='tile-value']").text()).toBe("$80K");
    expect(wrapper.find("[data-test='tile-won'] [data-test='tile-note']").text()).toBe(
      "8% of quota",
    );
    expect(wrapper.find("[data-test='meter-summary']").text()).toBe(
      "$80,000 of $1,000,000 closed",
    );
    const avery = wrapper
      .findAll("[data-test='rep-table'] tbody tr")
      .find((row) => row.text().includes("Avery Lee"));
    expect(avery.find("[data-test='attainment']").text()).toBe("16%");
  });
});
