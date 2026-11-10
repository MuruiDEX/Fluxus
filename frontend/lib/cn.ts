import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * Semantic type sizes are custom theme tokens, so tailwind-merge has to be
 * taught that they are font sizes. Without this it treats `text-body-sm` as a
 * text colour and drops it whenever a real colour class is present.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [
        {
          text: [
            "micro",
            "micro-sm",
            "label",
            "label-sm",
            "caption",
            "small",
            "body-sm",
            "body",
            "body-md",
            "body-lg",
            "display",
            "heading-xs",
            "heading-sm",
            "heading-md",
            "heading-lg",
            "display-sm",
          ],
        },
      ],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
