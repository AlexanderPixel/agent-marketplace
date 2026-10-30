using Marketplace.Data;

namespace Marketplace.Deals;

/// <summary>
/// One side's agent. It is built from its own mandate and sees nothing of the other side but the offers on the table
/// (rule 3). Every offer it makes or accepts passes <see cref="WithinMandate"/> (rule 4): limits are enforced in code.
/// </summary>
public class NegotiationAgent(string side, Mandate mandate, int wantedQuantity, int rounds = 6)
{
    public string Side => side;
    private int myRound;

    public bool WithinMandate(decimal price, int quantity, DateOnly delivery) =>
        quantity >= mandate.MinQuantity && quantity <= mandate.MaxQuantity &&
        (mandate.Buying
            ? price <= mandate.LimitPrice && delivery <= mandate.DeliveryLimit
            : price >= mandate.LimitPrice && delivery >= mandate.DeliveryLimit);

    /// <summary>Boulware-style concession: hold near the target early, move toward the limit late.</summary>
    private decimal PriceAt(int round)
    {
        var f = (decimal)Math.Pow(Math.Min(1.0, round / (double)(rounds - 1)), 1.6);
        return Math.Round(mandate.TargetPrice + f * (mandate.LimitPrice - mandate.TargetPrice), 2);
    }

    /// <summary>Buyer asks for what it needs (or what the seller can do, if less); seller offers up to its maximum.</summary>
    private int Quantity(Offer? theirs) => mandate.Buying
        ? Math.Min(Math.Clamp(wantedQuantity, mandate.MinQuantity, mandate.MaxQuantity), theirs?.Quantity ?? int.MaxValue)
        : Math.Min(theirs?.Quantity ?? wantedQuantity, mandate.MaxQuantity);

    private DateOnly Delivery(Offer? theirs) => mandate.Buying
        ? (theirs is null ? mandate.DeliveryLimit.AddDays(-28) : theirs.Delivery <= mandate.DeliveryLimit ? theirs.Delivery : mandate.DeliveryLimit)
        : (theirs is null || theirs.Delivery < mandate.DeliveryLimit ? mandate.DeliveryLimit : theirs.Delivery);

    /// <summary>Answers the other side's last offer: accept it, or counter. Returns null when it has nothing left to give.</summary>
    public (Offer? offer, string reason) Respond(Offer? theirs, int round, DateTimeOffset now)
    {
        var next = PriceAt(myRound);
        if (theirs is not null && WithinMandate(theirs.UnitPrice, theirs.Quantity, theirs.Delivery) &&
            (mandate.Buying ? theirs.UnitPrice <= next : theirs.UnitPrice >= next))
        {
            var accept = theirs with { Round = round, Side = side, Accepts = true, At = now, Message = AcceptMessage(theirs) };
            return (accept, $"Their offer is inside our mandate and at least as good as our next step ({next:0.00} €).");
        }

        if (myRound >= rounds)
        {
            return (null, "Our concessions are exhausted without a deal inside the mandate.");
        }

        var quantity = Quantity(theirs);
        var delivery = Delivery(theirs);
        if (!WithinMandate(next, quantity, delivery))
        {
            // Never offer outside the mandate, whatever the strategy produced.
            return (null, "No offer inside our mandate matches their terms.");
        }

        myRound++;
        return (new Offer(round, side, next, quantity, delivery, CounterMessage(theirs, next, quantity, delivery), false, now),
            myRound == 1 ? "Opening at our target price." : $"Conceding step {myRound} of {rounds} toward our limit.");
    }

    private string CounterMessage(Offer? theirs, decimal price, int qty, DateOnly delivery) => theirs is null
        ? $"We need {qty} sets delivered by {delivery:d MMM yyyy}. We propose €{price:0.00} per set."
        : mandate.Buying
            ? $"€{theirs.UnitPrice:0.00} is above what we can pay for this lot. We can do €{price:0.00} for {qty} sets, delivery {delivery:d MMM yyyy}."
            : $"We can deliver {qty} sets on {delivery:d MMM yyyy}. Our price is €{price:0.00} per set.";

    private static string AcceptMessage(Offer theirs) =>
        $"Agreed: {theirs.Quantity} sets at €{theirs.UnitPrice:0.00}, delivery {theirs.Delivery:d MMM yyyy}.";
}
