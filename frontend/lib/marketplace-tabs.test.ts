import { describe, expect, it } from "vitest";
import {
  isFreePrice,
  parseInstalls,
  selectListings,
  type TabbableListing,
} from "@/lib/marketplace-tabs";

const listings: TabbableListing[] = [
  {
    name: "AI Sales Manager",
    role: "Sales",
    blurb: "Qualify leads.",
    price: "$29/month",
    rating: 4.9,
    installs: "5.1k",
  },
  {
    name: "SEO Specialist AI",
    role: "SEO",
    blurb: "Keywords and audits.",
    price: "$19/month",
    rating: 4.8,
    installs: "1.8k",
    badge: "New",
  },
  {
    name: "Free Helper",
    role: "Support",
    blurb: "Zero cost starter.",
    price: "$0",
    rating: 4.2,
    installs: "950",
  },
];

describe("parseInstalls", () => {
  it("parses k/m suffixes and plain numbers", () => {
    expect(parseInstalls("2.1k")).toBe(2100);
    expect(parseInstalls("1.2m")).toBe(1_200_000);
    expect(parseInstalls("950")).toBe(950);
    expect(parseInstalls("1,204")).toBe(1204);
  });

  it("returns 0 for unparseable text", () => {
    expect(parseInstalls("")).toBe(0);
    expect(parseInstalls("new")).toBe(0);
  });
});

describe("isFreePrice", () => {
  it("treats zero amounts and 'free' as free", () => {
    expect(isFreePrice("$0")).toBe(true);
    expect(isFreePrice("$0.00/month")).toBe(true);
    expect(isFreePrice("Free")).toBe(true);
  });

  it("does not treat paid prices as free", () => {
    expect(isFreePrice("$19/month")).toBe(false);
    expect(isFreePrice("$99/month")).toBe(false);
  });
});

describe("selectListings", () => {
  it("keeps the catalog order for Recommended", () => {
    expect(selectListings(listings, "Recommended", "").map((l) => l.name)).toEqual([
      "AI Sales Manager",
      "SEO Specialist AI",
      "Free Helper",
    ]);
  });

  it("sorts Trending by parsed installs", () => {
    expect(selectListings(listings, "Trending", "").map((l) => l.name)).toEqual([
      "AI Sales Manager",
      "SEO Specialist AI",
      "Free Helper",
    ]);
  });

  it("sorts Best Rated by rating", () => {
    expect(selectListings(listings, "Best Rated", "").map((l) => l.rating)).toEqual([4.9, 4.8, 4.2]);
  });

  it("filters New by badge", () => {
    expect(selectListings(listings, "New", "").map((l) => l.name)).toEqual(["SEO Specialist AI"]);
  });

  it("splits Free and Premium by price", () => {
    expect(selectListings(listings, "Free", "").map((l) => l.name)).toEqual(["Free Helper"]);
    expect(selectListings(listings, "Premium", "").map((l) => l.name)).toEqual([
      "AI Sales Manager",
      "SEO Specialist AI",
    ]);
  });

  it("combines the tab with the search query", () => {
    expect(selectListings(listings, "Recommended", "seo").map((l) => l.name)).toEqual([
      "SEO Specialist AI",
    ]);
    expect(selectListings(listings, "Free", "sales")).toEqual([]);
  });

  it("never mutates the input array", () => {
    const snapshot = listings.map((l) => l.name);
    selectListings(listings, "Best Rated", "");
    expect(listings.map((l) => l.name)).toEqual(snapshot);
  });
});
