import * as React from "react";
import { cn } from "@/lib/utils";

export interface SliderProps
  extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  valueDisplay?: string;
  min?: number;
  max?: number;
  step?: number;
}

const Slider = React.forwardRef<HTMLInputElement, SliderProps>(
  ({ className, label, valueDisplay, min = 0, max = 100, step = 1, value, ...props }, ref) => {
    return (
      <div className="space-y-2">
        {(label || valueDisplay !== undefined) && (
          <div className="flex justify-between items-center text-xs">
            {label && <span className="text-text2 font-medium">{label}</span>}
            {valueDisplay !== undefined && (
              <span className="font-mono font-bold text-accent text-sm">
                {valueDisplay}
              </span>
            )}
          </div>
        )}
        <input
          type="range"
          ref={ref}
          className={cn(
            "w-full h-1 bg-[rgba(56,189,248,0.2)] rounded-lg appearance-none cursor-pointer",
            "accent-[#38bdf8]",
            "[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3",
            "[&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-accent",
            "[&::-webkit-slider-thumb]:cursor-pointer [&::-webkit-slider-thumb]:transition-all",
            "[&::-webkit-slider-thumb]:hover:bg-[#0ea5e9] [&::-webkit-slider-thumb]:hover:scale-125",
            className
          )}
          min={min}
          max={max}
          step={step}
          value={value}
          {...props}
        />
      </div>
    );
  }
);

Slider.displayName = "Slider";

export { Slider };
