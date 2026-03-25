// Excerpt from "A Midsummer Night's Dream" — Act II, Scene I
// Perfect for rehearsal demo: multiple characters, stage directions, varied line lengths

export const scriptMeta = {
  title: "A Midsummer Night's Dream",
  playwright: "William Shakespeare",
  act: "Act II",
  scene: "Scene I — A Wood Near Athens",
};

export const characters = {
  OBERON: { name: "Oberon", color: "#8B2035" },
  TITANIA: { name: "Titania", color: "#4A7C59" },
  PUCK: { name: "Puck", color: "#C49A3C" },
  FAIRY: { name: "Fairy", color: "#6B6770" },
};

export const scriptLines = [
  {
    id: 1,
    type: "stage_direction",
    text: "Enter a Fairy at one door, and Robin Goodfellow (Puck) at another.",
  },
  {
    id: 2,
    type: "dialogue",
    character: "PUCK",
    text: "How now, spirit! Whither wander you?",
  },
  {
    id: 3,
    type: "dialogue",
    character: "FAIRY",
    text: "Over hill, over dale, thorough bush, thorough brier, over park, over pale, thorough flood, thorough fire — I do wander everywhere, swifter than the moon's sphere; and I serve the Fairy Queen, to dew her orbs upon the green.",
  },
  {
    id: 4,
    type: "dialogue",
    character: "FAIRY",
    text: "The cowslips tall her pensioners be; in their gold coats spots you see. Those be rubies, fairy favours, in those freckles live their savours. I must go seek some dewdrops here, and hang a pearl in every cowslip's ear.",
  },
  {
    id: 5,
    type: "stage_direction",
    text: "Puck steps forward into the light, arms wide.",
  },
  {
    id: 6,
    type: "dialogue",
    character: "PUCK",
    text: "The King doth keep his revels here tonight. Take heed the Queen come not within his sight, for Oberon is passing fell and wrath, because that she, as her attendant, hath a lovely boy stolen from an Indian king.",
  },
  {
    id: 7,
    type: "dialogue",
    character: "PUCK",
    text: "She never had so sweet a changeling. And jealous Oberon would have the child knight of his train, to trace the forests wild. But she perforce withholds the loved boy, crowns him with flowers, and makes him all her joy.",
  },
  {
    id: 8,
    type: "stage_direction",
    text: "Enter Oberon from stage left, with his train. Enter Titania from stage right, with hers.",
  },
  {
    id: 9,
    type: "dialogue",
    character: "OBERON",
    text: "Ill met by moonlight, proud Titania.",
  },
  {
    id: 10,
    type: "dialogue",
    character: "TITANIA",
    text: "What, jealous Oberon? Fairies, skip hence. I have forsworn his bed and company.",
  },
  {
    id: 11,
    type: "dialogue",
    character: "OBERON",
    text: "Tarry, rash wanton! Am not I thy lord?",
  },
  {
    id: 12,
    type: "dialogue",
    character: "TITANIA",
    text: "Then I must be thy lady; but I know when thou hast stolen away from Fairyland, and in the shape of Corin sat all day, playing on pipes of corn, and versing love to amorous Phillida.",
  },
  {
    id: 13,
    type: "stage_direction",
    text: "Titania turns away. Oberon reaches toward her.",
  },
  {
    id: 14,
    type: "dialogue",
    character: "OBERON",
    text: "How canst thou thus, for shame, Titania, glance at my credit with Hippolyta, knowing I know thy love to Theseus?",
  },
  {
    id: 15,
    type: "dialogue",
    character: "TITANIA",
    text: "These are the forgeries of jealousy; and never, since the middle summer's spring, met we on hill, in dale, forest, or mead, by paved fountain or by rushy brook, or in the beached margent of the sea, to dance our ringlets to the whistling wind, but with thy brawls thou hast disturbed our sport.",
  },
  {
    id: 16,
    type: "stage_direction",
    text: "A long silence. The fairy train shifts uneasily.",
  },
  {
    id: 17,
    type: "dialogue",
    character: "OBERON",
    text: "Do you amend it, then; it lies in you. Why should Titania cross her Oberon? I do but beg a little changeling boy to be my henchman.",
  },
  {
    id: 18,
    type: "dialogue",
    character: "TITANIA",
    text: "Set your heart at rest. The Fairyland buys not the child of me. His mother was a votaress of my order, and in the spiced Indian air by night full often hath she gossiped by my side.",
  },
  {
    id: 19,
    type: "stage_direction",
    text: "Titania exits with her train. Oberon watches her go.",
  },
  {
    id: 20,
    type: "dialogue",
    character: "OBERON",
    text: "Well, go thy way. Thou shalt not from this grove till I torment thee for this injury. — My gentle Puck, come hither.",
  },
];

export const directorNotes = [
  {
    id: 1,
    lineId: 9,
    type: "pacing",
    severity: "suggestion",
    text: "Try a longer pause before 'proud Titania'. Let the audience feel the weight of their history before you name her.",
  },
  {
    id: 2,
    lineId: 3,
    type: "inflection",
    severity: "note",
    text: "This speech has a lilting, musical rhythm. Lean into the sing-song quality. You're a fairy, not a newsreader. Let 'swifter than the moon's sphere' really soar.",
  },
  {
    id: 3,
    lineId: 15,
    type: "emotion",
    severity: "important",
    text: "Titania is genuinely hurt here, not just angry. The 'forgeries of jealousy' line should crack slightly. She's recounting real damage to the natural world caused by their quarrel.",
  },
  {
    id: 4,
    lineId: 6,
    type: "blocking",
    severity: "suggestion",
    text: "Consider pacing stage-left as you describe Oberon's wrath. Physical movement mirrors the restless energy of the gossip Puck is sharing.",
  },
  {
    id: 5,
    lineId: 20,
    type: "pacing",
    severity: "important",
    text: "The shift from anger ('go thy way') to scheming ('My gentle Puck') should be sudden and chilling. Don't rush the transition. A beat of silence, then the smile.",
  },
];

// The character the user is rehearsing as
export const userCharacter = "OBERON";
