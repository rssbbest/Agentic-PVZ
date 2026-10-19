using System.Collections.Generic;

// NEW: run-wide unique "<PrefabName>_<n>" names + placed count (start-of-run and mid-run share it)
public class PlacementLedger
{
    readonly Dictionary<string, int> typeCounters = new Dictionary<string, int>();
    int placedCount;

    // returns "<prefabName>_<n>", n counted per prefab for the whole run
    public string RegisterPlacement(string prefabName)
    {
        int n = 0;
        if (typeCounters.ContainsKey(prefabName))
        {
            n = typeCounters[prefabName];
        }
        typeCounters[prefabName] = n + 1;
        placedCount++;
        return prefabName + "_" + n;
    }

    // total placements so far, never decreases
    public int PlacedCount()
    {
        return placedCount;
    }
}
