"""The Hitchhiker's Guide dashboard from the Prefab welcome page.

Run with:
    prefab serve examples/hitchhikers-guide/dashboard.py
    prefab export examples/hitchhikers-guide/dashboard.py
"""

from prefab_ui import PrefabApp
from prefab_ui.actions import SetInterval, SetState, ShowToast
from prefab_ui.components import (
    Alert,
    AlertDescription,
    AlertTitle,
    Badge,
    Button,
    Card,
    CardContent,
    CardDescription,
    CardFooter,
    CardHeader,
    CardTitle,
    Carousel,
    Checkbox,
    Column,
    Combobox,
    ComboboxOption,
    DataTable,
    DataTableColumn,
    DatePicker,
    Dialog,
    Grid,
    GridItem,
    HoverCard,
    Loader,
    Metric,
    Muted,
    P,
    Progress,
    Radio,
    RadioGroup,
    Ring,
    Row,
    Separator,
    Slider,
    Switch,
    Text,
    Tooltip,
)
from prefab_ui.components.charts import (
    BarChart,
    ChartSeries,
    RadarChart,
    Sparkline,
)
from prefab_ui.components.control_flow import Else, If
from prefab_ui.rx import Rx

ctx_tick = Rx("ctx_tick")

# Context window: climbs from 24% to ~78%, then resets
ctx_pct = (ctx_tick % 20) * 3 + 20
ctx_variant = (ctx_pct > 70).then(
    "destructive", (ctx_pct <= 33).then("success", "default")
)

with PrefabApp(
    title="Prefab Showcase",
    state={"ctx_tick": 0, "improbability": 42},
    on_mount=SetInterval(
        400,
        on_tick=SetState("ctx_tick", ctx_tick + 1),
    ),
) as app:
    with Grid(columns={"default": 1, "md": 2, "lg": 4}, gap=4):
        # ── Col 1 ─────────────────────────────────────────────────────────
        with Column(gap=4):
            with Card():
                with CardHeader():
                    CardTitle("Register Towel")
                    CardDescription("The most important item in the galaxy")
                with CardContent():
                    with Column(gap=3):
                        with Combobox(
                            placeholder="Type...",
                            search_placeholder="Search types...",
                        ):
                            ComboboxOption("Bath", value="bath")
                            ComboboxOption("Beach", value="beach")
                            ComboboxOption("Interstellar", value="interstellar")
                            ComboboxOption("Microfiber", value="micro")
                        DatePicker(placeholder="Registration date")
                with CardFooter():
                    with Row(gap=2):
                        with Dialog(
                            title="Towel Registered!",
                            description="Your towel has been added to the galactic registry.",
                        ):
                            Button("Register")
                            Text("Don't forget to bring it.")
                        Button("Cancel", variant="outline")
            with If("{{ !pressed }}"):
                Button(
                    "This is probably the best button to press.",
                    variant="success",
                    on_click=SetState("pressed", True),
                )
            with Else():
                Button(
                    "Please do not press this button again.",
                    variant="destructive",
                    on_click=SetState("pressed", False),
                )

            with Card():
                with CardHeader():
                    CardTitle("Ship Status")
                with CardContent():
                    with Column(gap=3):
                        with Row(
                            align="center",
                            css_class="justify-between",
                        ):
                            Text("heart-of-gold")
                            with HoverCard(open_delay=0, close_delay=200):
                                Badge("In Orbit", variant="default")
                                with Column(gap=2):
                                    Text("heart-of-gold")
                                    Muted("Deployed 2h ago")
                                    Progress(
                                        value=100,
                                        max=100,
                                        variant="success",
                                    )
                        Progress(
                            value=100,
                            max=100,
                            indicator_class="bg-yellow-400",
                        )
                        with Row(
                            align="center",
                            css_class="justify-between",
                        ):
                            Text("vogon-poetry")
                            with Tooltip("64% — ETA 12 min", delay=0):
                                with Badge(variant="secondary"):
                                    Loader(size="sm")
                                    Text("Deploying")
                        Progress(value=64, max=100)
                        with Row(
                            align="center",
                            css_class="justify-between",
                        ):
                            Text("deep-thought")
                            with Tooltip(
                                "Computing... 7.5 million years remaining",
                                delay=0,
                            ):
                                with Badge(variant="outline"):
                                    Loader(size="sm", variant="ios")
                                    Text("Soon...")
                        Progress(value=12, max=100)
            with Card():
                with CardHeader():
                    CardTitle("Planet Ratings")
                with CardContent():
                    RadarChart(
                        data=[
                            {"axis": "Views", "earth": 30, "mag": 95},
                            {"axis": "Fjords", "earth": 65, "mag": 100},
                            {"axis": "Pubs", "earth": 90, "mag": 10},
                            {"axis": "Mice", "earth": 40, "mag": 85},
                            {"axis": "Tea", "earth": 95, "mag": 15},
                            {"axis": "Safety", "earth": 45, "mag": 70},
                        ],
                        series=[
                            ChartSeries(dataKey="earth", label="Earth"),
                            ChartSeries(dataKey="mag", label="Magrathea"),
                        ],
                        axis_key="axis",
                        height=200,
                        show_legend=True,
                        show_tooltip=True,
                    )

        # ── Col 2 ─────────────────────────────────────────────────────────
        with Column(gap=4):
            with Card():
                with CardHeader():
                    CardTitle("Survival Odds")
                with CardContent(css_class="w-fit mx-auto"):
                    Ring(
                        value=42,
                        label="42%",
                        variant="info",
                        size="lg",
                        thickness=12,
                        indicator_class="group-hover:drop-shadow-[0_0_24px_rgba(59,130,246,0.9)]",
                    )
            with Card():
                with CardHeader():
                    with Row(gap=2, align="center"):
                        CardTitle("Improbability Drive")
                        Loader(
                            variant="pulse",
                            size="sm",
                            css_class="text-blue-500",
                        )
                with CardContent():
                    with Column(gap=2):
                        Slider(
                            min=0,
                            max=100,
                            value=42,
                            name="improbability",
                        )
                        with Row(
                            align="center",
                            css_class="justify-between",
                        ):
                            Muted("Probable")
                            Muted("Infinite")
            with Carousel(auto_advance=3000, show_controls=False, direction="up"):
                with Alert(variant="success", icon="circle-check"):
                    AlertTitle("Don't Panic")
                    AlertDescription("Normality achieved.")
                with Alert(variant="destructive", icon="triangle-alert"):
                    AlertTitle("Display Department")
                    AlertDescription("Beware of the leopard.")
            with Card():
                with CardHeader():
                    CardTitle("Prefect Horizon Config")
                with CardContent():
                    with Column(gap=3):
                        Switch(
                            label="Auto-scale agents",
                            value=True,
                            name="autoscale",
                        )
                        Separator()
                        Switch(
                            label="Code Mode",
                            value=True,
                            name="code_mode",
                        )
                        Separator()
                        Switch(
                            label="Tool call caching",
                            value=False,
                            name="cache",
                        )
                with CardFooter():
                    Button(
                        "Save Preferences",
                        on_click=ShowToast("Preferences saved!"),
                    )
            with Card():
                with CardHeader():
                    CardTitle("Travel Class")
                with CardContent():
                    with RadioGroup(name="travel_class"):
                        Radio(option="economy", label="Economy")
                        Radio(option="business", label="Business Class")
                        Radio(
                            option="improbability",
                            label="Infinite Improbability",
                            value=True,
                        )

        # ── Cols 3–4: summary row, chart, then 2-col grid below ─────────
        with GridItem(css_class="md:col-span-2"):
            with Column(gap=4):
                with Grid(columns=2, gap=4, css_class="h-32"):
                    with Card():
                        with CardHeader():
                            CardTitle("Context Window")
                        with CardContent():
                            with Column(
                                gap=6,
                                justify="center",
                                css_class="h-full",
                            ):
                                with Row(
                                    align="center",
                                    css_class="justify-between",
                                ):
                                    Text(f"{ctx_pct}% used")
                                    Muted(f"{ctx_pct * 2}k / 200k tokens")
                                with Tooltip(
                                    "Auto-compact buffer: 12%",
                                    delay=0,
                                ):
                                    Progress(
                                        value=ctx_pct,
                                        max=100,
                                        variant=ctx_variant,
                                    )
                    with Card(css_class="pb-0 gap-0"):
                        with CardContent():
                            Metric(
                                label="Fjords designed",
                                value="1,847",
                                delta="+3 coastlines",
                            )
                        Sparkline(
                            data=[
                                820,
                                950,
                                1100,
                                980,
                                1250,
                                1400,
                                1350,
                                1500,
                                1680,
                                1847,
                            ],
                            variant="success",
                            fill=True,
                            css_class="h-16",
                        )
                with Card():
                    with CardHeader():
                        CardTitle("Towel Incidents")
                    with CardContent():
                        BarChart(
                            data=[
                                {"month": "Jan", "lost": 8, "found": 5},
                                {"month": "Feb", "lost": 24, "found": 15},
                                {"month": "Mar", "lost": 12, "found": 28},
                                {"month": "Apr", "lost": 35, "found": 19},
                                {"month": "May", "lost": 18, "found": 38},
                                {"month": "Jun", "lost": 42, "found": 30},
                            ],
                            series=[
                                ChartSeries(dataKey="lost", label="Lost"),
                                ChartSeries(dataKey="found", label="Found"),
                            ],
                            x_axis="month",
                            height=200,
                            bar_radius=4,
                            show_legend=True,
                            show_tooltip=True,
                            show_grid=True,
                        )

                with Grid(columns=2, gap=4):
                    with Column(gap=4):
                        with Card():
                            with CardContent():
                                with Column(gap=2):
                                    Checkbox(label="Towel packed", value=True)
                                    Checkbox(label="Guide charged", value=True)
                                    Checkbox(
                                        label="Babel fish inserted",
                                        value=False,
                                    )
                        with Card():
                            with CardHeader():
                                CardTitle("Marvin's Mood")
                            with CardContent():
                                with Column(gap=3):
                                    P("How's life?")
                                    with Column(gap=2):
                                        Button(
                                            "Meh",
                                            on_click=ShowToast(
                                                "Noted. Enthusiasm levels nominal."
                                            ),
                                        )
                                        Button(
                                            "Depressed",
                                            variant="info",
                                            on_click=ShowToast(
                                                "I think you ought to "
                                                "know I'm feeling very "
                                                "depressed."
                                            ),
                                        )
                                        Button(
                                            "Don't talk to me about life",
                                            variant="warning",
                                            on_click=ShowToast(
                                                "Brain the size of a "
                                                "planet and they ask me "
                                                "to pick up a piece of "
                                                "paper."
                                            ),
                                        )

                    with Column(gap=4):
                        with Card():
                            with CardContent():
                                with Row(gap=2, align="center"):
                                    Loader(variant="dots", size="sm")
                                    Muted("Marvin is thinking...")
                        with Card():
                            with CardContent():
                                DataTable(
                                    columns=[
                                        DataTableColumn(
                                            key="crew",
                                            header="Crew",
                                            sortable=True,
                                        ),
                                        DataTableColumn(
                                            key="species",
                                            header="Species",
                                            sortable=True,
                                        ),
                                        DataTableColumn(
                                            key="towel",
                                            header="Towel?",
                                            sortable=True,
                                        ),
                                        DataTableColumn(
                                            key="status",
                                            header="Status",
                                            sortable=True,
                                        ),
                                    ],
                                    rows=[
                                        {
                                            "crew": "Arthur Dent",
                                            "species": "Human",
                                            "towel": "Yes",
                                            "status": "Confused",
                                        },
                                        {
                                            "crew": "Ford Prefect",
                                            "species": "Betelgeusian",
                                            "towel": "Always",
                                            "status": "Drinking",
                                        },
                                        {
                                            "crew": "Zaphod",
                                            "species": "Betelgeusian",
                                            "towel": "Lost it",
                                            "status": "Presidential",
                                        },
                                        {
                                            "crew": "Trillian",
                                            "species": "Human",
                                            "towel": "Yes",
                                            "status": "Navigating",
                                        },
                                        {
                                            "crew": "Marvin",
                                            "species": "Android",
                                            "towel": "No point",
                                            "status": "Depressed",
                                        },
                                        {
                                            "crew": "Slartibartfast",
                                            "species": "Magrathean",
                                            "towel": "Somewhere",
                                            "status": "Designing",
                                        },
                                    ],
                                    search=True,
                                    paginated=False,
                                )
