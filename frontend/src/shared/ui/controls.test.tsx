import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Chip } from "./chip";
import { Segmented } from "./segmented";
import { TabPanel, Tabs } from "./tabs";

function SegmentedDemo() {
  const [v, setV] = useState<"music" | "film">("music");
  return (
    <Segmented
      aria-label="Домен"
      value={v}
      onChange={setV}
      options={[
        { value: "music", label: "Музыка" },
        { value: "film", label: "Кино" },
      ]}
    />
  );
}

describe("Segmented", () => {
  it("выбран один вариант, клик переключает", async () => {
    render(<SegmentedDemo />);
    expect(screen.getByRole("radio", { name: "Музыка" })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: "Кино" }));
    expect(screen.getByRole("radio", { name: "Кино" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Музыка" })).not.toBeChecked();
  });

  it("стрелки переносят выбор и фокус, по кругу", async () => {
    render(<SegmentedDemo />);
    screen.getByRole("radio", { name: "Музыка" }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("radio", { name: "Кино" })).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("radio", { name: "Музыка" })).toBeChecked();
  });
});

function TabsDemo() {
  const [v, setV] = useState<"lobby" | "friends">("lobby");
  return (
    <>
      <Tabs
        aria-label="Онлайн"
        idBase="t"
        value={v}
        onChange={setV}
        tabs={[
          { value: "lobby", label: "Лобби" },
          { value: "friends", label: "Друзья" },
        ]}
      />
      <TabPanel idBase="t" value="lobby" active={v === "lobby"}>
        список лобби
      </TabPanel>
      <TabPanel idBase="t" value="friends" active={v === "friends"}>
        список друзей
      </TabPanel>
    </>
  );
}

describe("Tabs", () => {
  it("показывает панель выбранной вкладки и связывает её с вкладкой", async () => {
    render(<TabsDemo />);
    expect(screen.getByRole("tabpanel", { name: "Лобби" })).toHaveTextContent("список лобби");
    await userEvent.click(screen.getByRole("tab", { name: "Друзья" }));
    expect(screen.getByRole("tab", { name: "Друзья" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel", { name: "Друзья" })).toHaveTextContent("список друзей");
    expect(screen.queryByText("список лобби")).not.toBeInTheDocument();
  });

  it("стрелка вправо открывает следующую вкладку", async () => {
    render(<TabsDemo />);
    screen.getByRole("tab", { name: "Лобби" }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Друзья" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "Друзья" })).toHaveAttribute("aria-selected", "true");
  });
});

describe("Chip", () => {
  it("состояние выбора отдаётся через aria-pressed", () => {
    render(
      <>
        <Chip selected>Популярные</Chip>
        <Chip>Новые</Chip>
      </>,
    );
    expect(screen.getByRole("button", { name: "Популярные" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Новые" })).toHaveAttribute("aria-pressed", "false");
  });
});
