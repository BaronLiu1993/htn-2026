import Prototype from "./harness";

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ v?: string }>;
}) {
  const { v } = await searchParams;
  const variant = Number(v);

  return (
    <Prototype
      initialVariant={
        Number.isInteger(variant) && variant >= 1 && variant <= 4
          ? variant - 1
          : 0
      }
    />
  );
}
