"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

interface TabsProps {
  defaultValue: string;
  value?: string;
  onValueChange?: (value: string) => void;
  children: React.ReactNode;
  className?: string;
}

interface TabsListProps {
  children: React.ReactNode;
  className?: string;
}

interface TabsTriggerProps {
  value: string;
  children: React.ReactNode;
  className?: string;
}

interface TabsContentProps {
  value: string;
  children: React.ReactNode;
  className?: string;
}

const TabsContext = React.createContext<{
  value: string;
  onValueChange: (value: string) => void;
}>({
  value: "",
  onValueChange: () => {},
});

export function Tabs({ defaultValue, value, onValueChange, children, className }: TabsProps) {
  const [internalValue, setInternalValue] = React.useState(defaultValue);

  return (
    <TabsContext.Provider
      value={{
        value: value !== undefined ? value : internalValue,
        onValueChange: (val) => {
          onValueChange?.(val);
          if (value === undefined) setInternalValue(val);
        },
      }}
    >
      <div className={className}>{children}</div>
    </TabsContext.Provider>
  );
}

export function TabsList({ children, className }: TabsListProps) {
  return (
    <div
      className={cn(
        "inline-flex gap-1 p-1 bg-[rgba(0,20,50,0.5)] rounded-md",
        className
      )}
    >
      {children}
    </div>
  );
}

export function TabsTrigger({ value, children, className }: TabsTriggerProps) {
  const { value: currentValue, onValueChange } = React.useContext(TabsContext);
  const isActive = currentValue === value;

  return (
    <button
      onClick={() => onValueChange(value)}
      className={cn(
        "flex-1 px-3 py-1.5 text-xs font-semibold rounded transition-all",
        "text-muted hover:text-text2",
        isActive &&
          "bg-[rgba(56,189,248,0.18)] text-accent shadow-[inset_0_0_0_1px_rgba(56,189,248,0.3)]",
        className
      )}
    >
      {children}
    </button>
  );
}

export function TabsContent({ value, children, className }: TabsContentProps) {
  const { value: currentValue } = React.useContext(TabsContext);

  if (currentValue !== value) return null;

  return <div className={className}>{children}</div>;
}
